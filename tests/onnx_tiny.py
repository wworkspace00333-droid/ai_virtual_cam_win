"""Tiny real ONNX models for tests, written as protobuf bytes by hand (the `onnx` package is not a dependency).

Only what the tests need: float tensors, nodes with int / ints attributes, float initializers, opset 13. The files
are loaded and run by the real ONNX Runtime, so a test exercises the same path as a downloaded model.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

FLOAT, UINT8 = 1, 2


def _varint(v: int) -> bytes:
    v &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _int(field: int, v: int) -> bytes:
    return _key(field, 0) + _varint(v)


def _bytes(field: int, b: bytes | str) -> bytes:
    if isinstance(b, str):
        b = b.encode()
    return _key(field, 2) + _varint(len(b)) + b


def _value_info(name: str, shape, elem: int = FLOAT) -> bytes:
    dims = b"".join(_bytes(1, _int(1, d) if isinstance(d, int) else _bytes(2, str(d))) for d in shape)
    tensor = _int(1, elem) + _bytes(2, dims)
    return _bytes(1, name) + _bytes(2, _bytes(1, tensor))


def _attr(name: str, v) -> bytes:
    if isinstance(v, (list, tuple)):
        return _bytes(1, name) + b"".join(_int(8, int(x)) for x in v) + _int(20, 7)        # INTS
    if isinstance(v, float):
        import struct
        return _bytes(1, name) + _key(2, 5) + struct.pack("<f", v) + _int(20, 1)          # FLOAT
    return _bytes(1, name) + _int(3, int(v)) + _int(20, 2)                                # INT


def node(op: str, inputs: list[str], outputs: list[str], **attrs) -> bytes:
    return (b"".join(_bytes(1, i) for i in inputs) + b"".join(_bytes(2, o) for o in outputs) +
            _bytes(4, op) + b"".join(_bytes(5, _attr(k, v)) for k, v in attrs.items()))


def tensor(name: str, arr: np.ndarray) -> bytes:
    a = np.ascontiguousarray(arr, np.float32)
    return b"".join(_int(1, d) for d in a.shape) + _int(2, FLOAT) + _bytes(8, name) + _bytes(9, a.tobytes())


def model(path: Path, nodes: list[bytes], inputs: list[tuple], outputs: list[tuple],
          inits: dict[str, np.ndarray] | None = None) -> Path:
    """inputs / outputs: (name, shape[, elem type]); a str in a shape is a dynamic dimension."""
    g = b"".join(_bytes(1, n) for n in nodes) + _bytes(2, "g")
    g += b"".join(_bytes(5, tensor(k, v)) for k, v in (inits or {}).items())
    g += b"".join(_bytes(11, _value_info(*i)) for i in inputs)
    g += b"".join(_bytes(12, _value_info(*o)) for o in outputs)
    m = _int(1, 8) + _bytes(2, "vcstudio-tests") + _bytes(7, g) + _bytes(8, _bytes(1, "") + _int(2, 13))
    path = Path(path)
    path.write_bytes(m)
    return path


def conv1x1(path: Path, weight: np.ndarray, bias: np.ndarray | None = None, side=64, nhwc: bool = False,
            out_nhwc: bool = False) -> Path:
    """A 1x1 convolution image -> C maps: weight (C, 3). NHWC input / output via Transpose nodes."""
    c = weight.shape[0]
    w = weight.reshape(c, 3, 1, 1)
    inits = {"W": w}
    ins = ["xc", "W"]
    if bias is not None:
        inits["B"] = np.asarray(bias, np.float32)
        ins.append("B")
    nodes = []
    if nhwc:
        nodes.append(node("Transpose", ["x"], ["xc"], perm=[0, 3, 1, 2]))
    nodes.append(node("Conv", ["x" if not nhwc else "xc", *ins[1:]], ["yc" if out_nhwc else "y"]))
    if out_nhwc:
        nodes.append(node("Transpose", ["yc"], ["y"], perm=[0, 2, 3, 1]))
    s = side
    in_shape = [1, s, s, 3] if nhwc else [1, 3, s, s]
    out_shape = [1, s, s, c] if out_nhwc else [1, c, s, s]
    return model(path, nodes, [("x", in_shape)], [("y", out_shape)], inits)
