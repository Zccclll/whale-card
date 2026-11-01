# -*- coding: utf-8 -*-
"""读 PyTorch 的 pytorch_model.bin（纯标准库，不需要装 torch）。

为什么需要这个文件：
    bge-small-zh-v1.5 官方仓库有 model.safetensors，直接用 `_load_safetensors` 读。
    但 bge-base-zh-v1.5 **只有 pytorch_model.bin**（2026-10-30 实测），
    而 embed_np 的设计前提是「这台机器上不许有 torch」（409MB 的依赖为了读
    90MB 权重，不划算；卡是要插到别人电脑上就能跑的）。
    所以这里手写一个 .bin 读取器。

.pth / .bin 的格式（torch.save 默认 zip 容器，PyTorch 1.6+）：
    整个文件是个 zip，里面有：
      data.pkl                  —— pickle，键名 -> torch._utils._rebuild_tensor_v2(...)
      data/<key>                —— 每个张量的裸字节，按行主序排
    另外（1.6 之前的老格式）是裸 pickle 流，这里不支持——官方仓库是新格式。

    关键点：pickle 里关心的是 `_rebuild_tensor_v2(storage, offset, size, stride,
    requires_grad, backward_hooks)`，其中 storage 是一个
    `_TypedStorage(_UntypedStorage(...))` 对象，它只携带「在哪个文件里」的
    引用，真正的字节要从 zip 里按 key 读出来。

    本模块的做法：
      1. 用 zipfile 打开；
      2. 自定义 Unpickler，把 `_rebuild_tensor_v2` / `_TypedStorage` 等
         替换成「只记录元数据」的占位类；
      3. 按 size/stride/offset 从对应 zip 成员的裸字节里切出 numpy 数组。

    stride 不能忽略：BERT 的 Linear 权重是 (out, in) 连续存储，但有些层的
    权重（尤其是 transpose 过的）不是标准连续布局。用 np.lib.stride_tricks
    按元素步长取，等价于 torch 的 as_strided。
"""
import io
import os
import pickle
import zipfile

import numpy as np

# pytorch .bin 里存的 dtype 名字 -> numpy
_DTYPES = {
    'FloatStorage': np.float32,
    'DoubleStorage': np.float64,
    'HalfStorage': np.float16,
    'LongStorage': np.int64,
    'IntStorage': np.int32,
    'ShortStorage': np.int16,
    'CharStorage': np.int8,
    'ByteStorage': np.uint8,
    'BoolStorage': np.bool_,
    'BFloat16Storage': None,      # 需要特殊处理，见 _to_numpy
}


class _StorageRef(object):
    """替代 torch 的 _UntypedStorage/_TypedStorage，只记元数据。"""

    def __init__(self, key=None, dtype_name='FloatStorage', size=0):
        self.key = key
        self.dtype_name = dtype_name
        self.size = size

    def __repr__(self):
        return '<StorageRef %s %s %r>' % (self.key, self.dtype_name, self.size)


class _TensorRef(object):
    """替代 torch.Tensor，只记「这块张量怎么取」。"""

    def __init__(self, storage, offset, size, stride):
        self.storage = storage
        self.offset = offset            # 元素偏移
        self.size = tuple(size)
        self.stride = tuple(stride)


def _rebuild_tensor_v2(storage, storage_offset, size, stride,
                       requires_grad=False, backward_hooks=None, *a, **k):
    return _TensorRef(storage, int(storage_offset), size, stride)


def _rebuild_tensor(storage, storage_offset, size, stride, *a, **k):
    return _TensorRef(storage, int(storage_offset), size, stride)


class _StorageType(object):
    """torch 老格式里 storage_type 是 torch.FloatStorage 这类对象。"""

    def __init__(self, name='FloatStorage'):
        self.name = name

    def __call__(self, *a, **k):
        return _StorageRef(dtype_name=self.name)


def _rebuild_from_type_v2(func, new_type, args, state, *a, **k):
    return func(*args) if callable(func) else None


class _Unpickler(pickle.Unpickler):
    """拦截 torch 的对象，换成只记元数据的占位类。

    🔧 关键：torch.save 用的是 **persistent_id** 机制——
        pickle 里张量不是直接内联的，而是 `PERSID` 指令 + 一个元组
        `('storage', storage_type, key, location, numel)`，
        真正的字节在 zip 的 `data/<key>` 里。
        所以必须实现 `persistent_load`，否则会报
        `UnpicklingError: A load persistent id instruction was encountered,
         but no persistent_load function was specified.`
    """

    def persistent_load(self, pid):
        # pid 的形状（torch 源码 torch/serialization.py _load）：
        #   ('storage', storage_type, key, location, numel)
        #   或 'module' 老格式
        if not isinstance(pid, tuple):
            return _StorageRef()
        if pid[0] == 'storage':
            _tag, stype, key, _location, numel = pid[:5]
            dtype_name = getattr(stype, 'name', None) or 'FloatStorage'
            return _StorageRef(key=str(key), dtype_name=dtype_name,
                               size=int(numel))
        if pid[0] == 'module':
            return None
        return _StorageRef()

    def find_class(self, module, name):
        # torch 的对象全部拦掉，换成我们的占位类
        if name in ('_rebuild_tensor_v2', '_rebuild_tensor_v3'):
            return _rebuild_tensor_v2
        if name in ('_rebuild_tensor',):
            return _rebuild_tensor
        if name in ('_TypedStorage', '_UntypedStorage', 'TypedStorage',
                    'UntypedStorage'):
            return _StorageRef
        if name.endswith('Storage') and name in _DTYPES:
            return _StorageType(name)
        if name == '_rebuild_from_type_v2':
            return _rebuild_from_type_v2
        if module.startswith('torch'):
            # torch.Size / torch.dtype 之类，退化成普通容器
            if name in ('Size', 'size'):
                return tuple
            return lambda *a, **k: a[0] if len(a) == 1 else a
        return pickle.Unpickler.find_class(self, module, name)


def _to_numpy(raw, dtype_name, count):
    """裸字节 -> ndarray"""
    if dtype_name == 'BFloat16Storage':
        arr = np.frombuffer(raw, dtype=np.uint16)[:count]
        # bf16 -> f32：左移 16 位
        return (arr.astype(np.uint32) << 16).view(np.float32)
    dt = _DTYPES.get(dtype_name)
    if dt is None:
        raise ValueError('不认识的 dtype：%s' % dtype_name)
    return np.frombuffer(raw, dtype=dt)


def load(path):
    """读 .bin，返回 dict[str, np.ndarray]。

    键名与 state_dict 一致（`embeddings.word_embeddings.weight` 这种），
    和 safetensors 加载器返回的结构可以直接互换。
    """
    if not os.path.exists(path):
        raise IOError('文件不存在：%s' % path)

    with zipfile.ZipFile(path, 'r') as zf:
        names = zf.namelist()
        pkl = None
        for cand in ('data.pkl', 'archive/data.pkl', 'pytorch_model/data.pkl'):
            if cand in names:
                pkl = cand
                break
        if pkl is None:
            pkl = next((n for n in names if n.endswith('.pkl')), None)
        if pkl is None:
            raise ValueError('这不是 torch.save 的 zip 格式：%s' % path)
        root = pkl[:-len('data.pkl')] if pkl.endswith('data.pkl') else ''

        with zf.open(pkl) as f:
            obj = _Unpickler(io.BytesIO(f.read())).load()

        # 收集「storage key -> 裸字节」，按需读，避免 400MB 全进内存两次
        # 🔧 目录结构实测（bge-base-zh-v1.5 / torch.save）：
        #     pytorch_model/data.pkl
        #     pytorch_model/data/0
        #     pytorch_model/data/1 ...
        #   所以 key='0' 要拼成 root + 'data/0'，不是 root + '0'。
        def raw_of(ref):
            key = ref.key
            if key is None:
                return b''
            cands = [
                root + 'data/' + key,        # 标准布局（实测就是这个）
                root + key,                  # 老布局
                key,                         # 无前缀
                'archive/data/' + key,
            ]
            for m in cands:
                if m in names:
                    return zf.read(m)
            raise KeyError('找不到 storage %r，候选 %r；实际成员示例 %r'
                           % (key, cands[:2], names[:3]))

        out = {}
        for k, v in obj.items():
            if not isinstance(v, _TensorRef):
                # 非张量（很少见，比如 _metadata），跳过
                if isinstance(v, np.ndarray):
                    out[k] = v
                continue
            st = v.storage
            raw = raw_of(st)
            base = _to_numpy(raw, st.dtype_name, len(raw) // np.dtype(
                np.float32 if _DTYPES.get(st.dtype_name) is None
                else _DTYPES[st.dtype_name]).itemsize)
            if not v.size:
                out[k] = np.array(0, dtype=base.dtype)
                continue
            if all(s > 0 for s in v.stride):
                itemsize = base.itemsize
                view = np.lib.stride_tricks.as_strided(
                    base[v.offset:],
                    shape=v.size,
                    strides=tuple(s * itemsize for s in v.stride),
                    writeable=False,
                )
            else:
                view = base[v.offset:v.offset + int(np.prod(v.size))]
                view = view.reshape(v.size)
            out[k] = np.array(view, dtype=np.float32, copy=True)

    # 去掉 torch 常见的 'module.' 前缀
    clean = {}
    for k, v in out.items():
        clean[k[7:] if k.startswith('module.') else k] = v
    return clean


def main():
    import sys
    if len(sys.argv) < 2:
        print('用法：python load_torch_bin.py <path/to/pytorch_model.bin>')
        return 2
    w = load(sys.argv[1])
    print('张量数：%d' % len(w))
    total = sum(v.nbytes for v in w.values())
    print('总大小：%.1f MB' % (total / 1048576.0))
    for k in list(w)[:8]:
        print('  %-52s %s %s' % (k, w[k].shape, w[k].dtype))
    print('  ...')
    for k in list(w)[-3:]:
        print('  %-52s %s %s' % (k, w[k].shape, w[k].dtype))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
