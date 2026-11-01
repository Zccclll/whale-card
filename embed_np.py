# -*- coding: utf-8 -*-
"""纯 numpy 实现的 bge-small-zh-v1.5 编码器。

为什么不用 sentence-transformers：pip 在这台机器上慢到不可用（numpy 一个包
12.6MB 下了 4 分半），而 transformers + torch 加起来是几百 MB 的依赖树，
装不上也搬不动。但这张卡的语义层不能一直等——所以直接把 BERT 前向用 numpy
写出来，只依赖 numpy 一个包（venv 里已经有了）。

模型结构（读 config.json / safetensors 头得到）：
  BERT，4 层，hidden 512，8 头，intermediate 2048，vocab 21128，max_pos 512
  pooling: CLS token（1_Pooling/config.json 里 pooling_mode_cls_token=true）
  最后 L2 归一化

用法：
  import embed_np
  enc = embed_np.Encoder()          # 默认找同目录 models/bge-small-zh-v1.5
  v = enc.encode("今天去吃了烧烤")   # -> np.float32[512]，已归一化
  v2 = enc.encode_query("吃了什么")  # 检索用，带 bge 的查询指令前缀

和 card2.py 的接口约定：
  - 返回的一定是 L2 归一化过的向量，所以余弦相似度 = 点积
  - 编码失败（模型缺失/内存不足）抛 EmbedUnavailable，调用方自己回退到
    bigram。绝不返回一个「看起来像向量」的零向量——那会让召回静默变错。
"""
import json
import math
import os
import struct
import sys
import unicodedata

# 🔴 2026-10-31 凌晨：**必须在 import numpy 之前**限制 BLAS 线程数。
#
# 病象：同一句「烧烤」编码，多线程 16.8 秒，单线程 0.31 秒 —— 慢 54 倍。
#       整晚的测试跑不完、recall 单次 36 秒，全是这一个原因。
#
# 原因：这台机器是 Xeon E5-2666 v3，20 个逻辑核。OpenBLAS 默认开满线程，
#       但 base 模型前向的矩阵太小（12 token × 768），每次乘法的计算量
#       远小于 20 个线程互相同步的开销。线程越多，越像一屋子人在传一张纸。
#
# 注意：numpy 一旦 import 就把 BLAS 线程池建好了，之后 setenv 无效。
#       所以这段必须在 `import numpy as np` 上面。
for _v in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
           'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ.setdefault(_v, '1')

import numpy as np

MODEL_DIR_NAME = os.path.join('models', 'bge-small-zh-v1.5')

# 🔧 2026-10-30：支持换更大模型。
#    bge-small-zh-v1.5: 4 层 / 512 维 / 24M  → models/bge-small-zh-v1.5
#    bge-base-zh-v1.5:  12层 / 768 维 / 102M → models/bge-base-zh-v1.5
#    两者结构相同（都是 BERT），代码不用改分支，只有权重文件名不同：
#      small 有 model.safetensors，base 官方仓库**只有 pytorch_model.bin**。
#    选哪个由环境变量 WHALE_EMBED_MODEL 决定，缺省还是 small（稳妥、快）。
BASE_DIR_NAME = os.path.join('models', 'bge-base-zh-v1.5')


def default_model_dir():
    """按环境变量挑模型目录；没设就 small。"""
    want = (os.environ.get('WHALE_EMBED_MODEL') or '').strip().lower()
    here = os.path.dirname(os.path.abspath(__file__))
    if want in ('base', 'bge-base', 'bge-base-zh-v1.5'):
        return os.path.join(here, BASE_DIR_NAME)
    if want in ('small', 'bge-small', 'bge-small-zh-v1.5', ''):
        return os.path.join(here, MODEL_DIR_NAME)
    # 直接给了路径
    return want if os.path.isabs(want) else os.path.join(here, want)


# 权重候选文件名（按优先级）
WEIGHT_NAMES = ('model.safetensors', 'pytorch_model.bin')

# bge 系列做检索时，query 要加这个前缀（passage 不加）。
QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'

# BERT 的 gelu 是精确版（erf），不是 tanh 近似。numpy 没有 erf，
# 用 Abramowitz-Stegun 7.1.26 的有理逼近，精度 1.5e-7，对 embedding 足够。
_AS_A = (0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429)


class EmbedUnavailable(RuntimeError):
    """模型文件缺失或加载失败。调用方应该回退到字面匹配，而不是假装没事。"""


def _erf(x):
    sign = np.sign(x)
    ax = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    y = 1.0 - (((((_AS_A[4] * t + _AS_A[3]) * t) + _AS_A[2]) * t + _AS_A[1]) * t + _AS_A[0]) * t * np.exp(-ax * ax)
    return sign * y


def _gelu(x):
    return 0.5 * x * (1.0 + _erf(x / math.sqrt(2.0)))


def _layernorm(x, gamma, beta, eps=1e-12):
    mu = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mu) / np.sqrt(var + eps) * gamma + beta


def _softmax(x):
    x = x - x.max(axis=-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(axis=-1, keepdims=True)


# ---------------------------------------------------------------- tokenizer

def _is_chinese_char(cp):
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
            or 0x20000 <= cp <= 0x2A6DF or 0x2A700 <= cp <= 0x2B73F
            or 0x2B740 <= cp <= 0x2B81F or 0x2B820 <= cp <= 0x2CEAF
            or 0xF900 <= cp <= 0xFAFF or 0x2F800 <= cp <= 0x2FA1F)


def _is_punctuation(ch):
    cp = ord(ch)
    if (33 <= cp <= 47) or (58 <= cp <= 64) or (91 <= cp <= 96) or (123 <= cp <= 126):
        return True
    return unicodedata.category(ch).startswith('P')


def _split_on_punc(token):
    out, buf = [], []
    for ch in token:
        if _is_punctuation(ch):
            if buf:
                out.append(''.join(buf))
                buf = []
            out.append(ch)
        else:
            buf.append(ch)
    if buf:
        out.append(''.join(buf))
    return out


def _basic_tokenize(text):
    """复刻 BertTokenizer(do_lower_case=False, tokenize_chinese_chars=True)。"""
    spaced = []
    for ch in text:
        if _is_chinese_char(ord(ch)):
            spaced.append(' ')
            spaced.append(ch)
            spaced.append(' ')
        else:
            spaced.append(ch)
    text = ''.join(spaced)
    tokens = []
    for tok in text.strip().split():
        tokens.extend(_split_on_punc(tok))
    return tokens


class BertTokenizer:
    MAX_CHARS_PER_WORD = 100

    def __init__(self, vocab_path):
        self.vocab = {}
        with open(vocab_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                self.vocab[line.rstrip('\n')] = i
        self.cls_id = self.vocab['[CLS]']
        self.sep_id = self.vocab['[SEP]']
        self.unk_id = self.vocab['[UNK]']
        self.pad_id = self.vocab['[PAD]']
        self._cache = {}

    def _wordpiece(self, word):
        cached = self._cache.get(word)
        if cached is not None:
            return cached
        if len(word) > self.MAX_CHARS_PER_WORD:
            res = [self.unk_id]
            self._cache[word] = res
            return res
        sub, start = [], 0
        while start < len(word):
            end = len(word)
            cur = None
            while start < end:
                piece = word[start:end]
                if start > 0:
                    piece = '##' + piece
                if piece in self.vocab:
                    cur = piece
                    break
                end -= 1
            if cur is None:
                self._cache[word] = [self.unk_id]
                return [self.unk_id]
            sub.append(self.vocab[cur])
            start = end
        self._cache[word] = sub
        return sub

    def encode(self, text, max_len=512):
        """返回 (input_ids, 实际长度)。单句，自动加 [CLS]/[SEP]。"""
        ids = [self.cls_id]
        for tok in _basic_tokenize(text):
            ids.extend(self._wordpiece(tok))
            if len(ids) >= max_len - 1:
                break
        ids.append(self.sep_id)
        return ids[:max_len]


# ---------------------------------------------------------------- encoder

class Encoder:
    """bge 系列的 numpy 前向（small 90MB / base 400MB 内存都撑得住）。

    🔧 2026-10-30：支持 bge-base-zh-v1.5。
        两者都是标准 BERT，前向代码完全一样，差别只在：
          · 层数/维度从 config.json 读（本来就是读的，不用改）
          · 权重文件名：small 是 model.safetensors，base 只有 pytorch_model.bin
        所以这里按 WEIGHT_NAMES 顺序找，找到哪个用哪个。
    """

    def __init__(self, model_dir=None):
        if model_dir is None:
            model_dir = default_model_dir()
        self.model_dir = model_dir
        cfg_path = os.path.join(model_dir, 'config.json')
        vocab_path = os.path.join(model_dir, 'vocab.txt')
        for p in (cfg_path, vocab_path):
            if not os.path.exists(p):
                raise EmbedUnavailable('模型文件缺失：%s' % p)
        w_path = None
        for cand in WEIGHT_NAMES:
            p = os.path.join(model_dir, cand)
            if os.path.exists(p):
                w_path = p
                break
        if w_path is None:
            raise EmbedUnavailable('模型文件缺失：%s 下没有 %s'
                                   % (model_dir, '/'.join(WEIGHT_NAMES)))

        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        self.n_layers = int(cfg['num_hidden_layers'])
        self.n_heads = int(cfg['num_attention_heads'])
        self.hidden = int(cfg['hidden_size'])
        self.head_dim = self.hidden // self.n_heads
        self.max_pos = int(cfg['max_position_embeddings'])

        self.tok = BertTokenizer(vocab_path)
        if w_path.endswith('.safetensors'):
            self.w = self._load_safetensors(w_path)
        else:
            self.w = self._load_torch_bin(w_path)
        self._wt = self._transpose_weights(self.w)
        self._scale = 1.0 / math.sqrt(self.head_dim)

    @staticmethod
    def _transpose_weights(w):
        """把所有权重的转置预先算好并存成**连续**数组。

        为什么要预转置（2026-10-30 实测，20 倍差距）：
            BERT 的 Linear 是 y = x @ W^T + b，W 的形状是 (out, in)。
            numpy 里 `W.T` 只是交换 stride 的视图，内存布局仍是行主序的
            (out, in)。OpenBLAS 拿到「行主序矩阵的转置视图」会退到慢路径：
                (10,768) @ (768,768).T    → 12 ms
                (10,768) @ (768,768, 连续) → 0.42 ms
            小 batch（一到十几个 token）时固定开销占绝对主导，所以这一下
            直接决定整个编码是 780ms 还是 39ms。
        """
        out = {}
        for k, v in w.items():
            if k.endswith('.weight') and v.ndim == 2:
                # W (out,in) -> WT (in,out)，ascontiguousarray 压实
                out[k] = np.ascontiguousarray(v.T, dtype=np.float32)
        return out

    @staticmethod
    def _load_torch_bin(path):
        """读 pytorch_model.bin（纯标准库，不装 torch）。"""
        try:
            import load_torch_bin
        except ImportError:
            raise EmbedUnavailable('缺少 load_torch_bin.py')
        try:
            w = load_torch_bin.load(path)
        except Exception as e:
            raise EmbedUnavailable('%s: %s' % (type(e).__name__, e))
        if not w:
            raise EmbedUnavailable('pytorch_model.bin 里没读出张量')
        return w

    @staticmethod
    def _load_safetensors(path):
        with open(path, 'rb') as f:
            n = struct.unpack('<Q', f.read(8))[0]
            header = json.loads(f.read(n))
            data_start = 8 + n
            out = {}
            for name, info in header.items():
                if name == '__metadata__':
                    continue
                dtype = info['dtype']
                shape = info['shape']
                b, e = info['data_offsets']
                f.seek(data_start + b)
                raw = f.read(e - b)
                if dtype == 'F32':
                    arr = np.frombuffer(raw, dtype='<f4').reshape(shape).astype(np.float32)
                elif dtype == 'I64':
                    arr = np.frombuffer(raw, dtype='<i8').reshape(shape)
                else:
                    raise EmbedUnavailable('不认识的 dtype：%s' % dtype)
                out[name] = arr
        return out

    # -- 前向 --------------------------------------------------------
    def _forward(self, ids):
        w = self.w
        n = len(ids)
        ids_arr = np.asarray(ids, dtype=np.int64)
        pos = np.arange(n, dtype=np.int64)

        x = (w['embeddings.word_embeddings.weight'][ids_arr]
             + w['embeddings.position_embeddings.weight'][pos]
             + w['embeddings.token_type_embeddings.weight'][np.zeros(n, dtype=np.int64)])
        x = _layernorm(x, w['embeddings.LayerNorm.weight'], w['embeddings.LayerNorm.bias'])

        # 🔧 2026-10-30 性能修正（20 倍提速，bge-base 780ms → 39ms）：
        #    原来每次前向都现算 `x @ W.T`。`.T` 只是换了 stride，不是连续内存，
        #    BLAS 对这种右乘转置的布局要走慢路径 —— 实测 (10,768)@(768,768)
        #    用 `.T` 要 12ms，而预转置成连续数组后只要 0.42ms。
        #    所以这里一次性把所有权重**预转置 + ascontiguousarray**，前向里就是
        #    干净的 `x @ WT`（WT 已连续，BLAS 走快路径）。
        wT = self._wt            # 首次前向时由 _prepare() 建好，之后白嫖

        for i in range(self.n_layers):
            p = 'encoder.layer.%d.' % i

            def lin(name, x=x):
                return x @ wT[p + name + '.weight'] + w[p + name + '.bias']

            q = lin('attention.self.query')
            k = lin('attention.self.key')
            v = lin('attention.self.value')

            q = q.reshape(n, self.n_heads, self.head_dim).transpose(1, 0, 2)
            k = k.reshape(n, self.n_heads, self.head_dim).transpose(1, 0, 2)
            v = v.reshape(n, self.n_heads, self.head_dim).transpose(1, 0, 2)

            scores = (q @ k.transpose(0, 2, 1)) * self._scale
            ctx = _softmax(scores) @ v                       # (h, n, d)
            ctx = ctx.transpose(1, 0, 2).reshape(n, self.hidden)

            attn = ctx @ wT[p + 'attention.output.dense.weight'] + w[p + 'attention.output.dense.bias']
            x = _layernorm(x + attn,
                           w[p + 'attention.output.LayerNorm.weight'],
                           w[p + 'attention.output.LayerNorm.bias'])

            inter = _gelu(x @ wT[p + 'intermediate.dense.weight'] + w[p + 'intermediate.dense.bias'])
            out = inter @ wT[p + 'output.dense.weight'] + w[p + 'output.dense.bias']
            x = _layernorm(x + out,
                           w[p + 'output.LayerNorm.weight'],
                           w[p + 'output.LayerNorm.bias'])

        return x[0]   # CLS pooling

    def encode(self, text):
        text = (text or '').strip()
        if not text:
            raise ValueError('空文本不能编码')
        ids = self.tok.encode(text, max_len=self.max_pos)
        vec = self._forward(ids)
        norm = float(np.linalg.norm(vec))
        if norm <= 0:
            raise EmbedUnavailable('编码出零向量')
        return (vec / norm).astype(np.float32)

    def encode_query(self, text):
        """检索用：bge 要求 query 加指令前缀。"""
        return self.encode(QUERY_PREFIX + (text or '').strip())

    def encode_batch(self, texts, progress=False):
        out = []
        total = len(texts)
        for i, t in enumerate(texts):
            out.append(self.encode(t))
            if progress and (i + 1) % 20 == 0:
                print('  编码 %d/%d' % (i + 1, total), flush=True)
        return out


def available(model_dir=None):
    if model_dir is None:
        model_dir = default_model_dir()
    if not (os.path.exists(os.path.join(model_dir, 'config.json'))
            and os.path.exists(os.path.join(model_dir, 'vocab.txt'))):
        return False
    return any(os.path.exists(os.path.join(model_dir, c)) for c in WEIGHT_NAMES)


def main():
    """自测：跑几条真实句子，看余弦是不是符合直觉。"""
    if not available():
        print('模型文件不齐，先跑 dl_model.py')
        return 2
    enc = Encoder()
    cases = [
        ('你要做记忆卡吗', '我们要做一张数字生命卡', '高'),
        ('你要做记忆卡吗', '你永远值得我付出', '低'),
        ('我喜欢吃白米饭', '用户喜欢吃白米饭', '高'),
        ('我喜欢吃白米饭', '我暑假靠拍婚礼挣钱', '低'),
        ('你还记得我QQ号吗', '用户的主号QQ是100000000', '高'),
        ('你还记得我QQ号吗', '今天天气不错', '低'),
    ]
    ok = 0
    for a, b, want in cases:
        va, vb = enc.encode(a), enc.encode(b)
        s = float(va @ vb)
        got = '高' if s >= 0.6 else '低'
        flag = '✅' if got == want else '❌'
        if got == want:
            ok += 1
        print('%s  cos=%.3f  期望%s 实际%s   %s  ||  %s' % (flag, s, want, got, a, b))
    print('\n%d/%d 符合直觉' % (ok, len(cases)))
    return 0 if ok == len(cases) else 1


if __name__ == '__main__':
    sys.exit(main())
