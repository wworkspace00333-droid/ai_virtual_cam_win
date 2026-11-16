// vcs_d3d11.dll — Direct3D 11 compositor for AI Virtual Camera Studio on Windows.
//
// Same maths as the CPU backend (vcstudio/replacement/compositor.py, CpuBackend) and the Metal backend:
//   composite:  inner = src + bg * (1 - src.a);   out = k * inner + dst * (1 - k * inner.a)
//   effect:     out = k * effect(input) + dst * (1 - k)          (k = opacity * mask)
// Effects read the *input* frame; layers are applied in order; the frame's alpha channel is kept.
//
// Implementation: compute shaders, which behave the same on every feature-level-11_0 GPU and on WARP.
//   frame   R32_UINT textures (BGRA bytes packed in one uint): typed UAV load+store of R32_UINT is
//           guaranteed on FL 11_0, unlike UNORM formats. One SRV copy of the input, one UAV work copy.
//   assets  R8G8B8A8_UNORM textures holding BGRA bytes (so .rgb is B, G, R), bilinear, border = 0
//   masks   R8_UNORM textures, bilinear, border = 0
//   blur    separable Gaussian (kernel as cv2.GaussianBlur for 8-bit images) into a float texture
// The Python side (vcstudio/platform/win/d3d11.py) passes a 28-float layer record per draw op, the same
// layout the Metal backend uses (bg colour in BGRA order here).
//
// C API (ctypes): see the extern "C" block at the end. Thread use: one context per thread.

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <d3d11.h>
#include <d3dcompiler.h>
#include <dxgi1_2.h>
#include <wrl/client.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <map>
#include <string>
#include <vector>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3dcompiler.lib")

using Microsoft::WRL::ComPtr;

static const char *kShader = R"HLSL(
struct Layer {
    float4 bbox;      // x0 y0 x1 y1 (frame px, integer values)
    float4 inv;       // a b c d : asset px = [a b; c d] * frame px + t
    float4 tsz;       // tx ty assetW assetH
    float4 bg;        // premultiplied fill, BGRA order, 0..1
    float4 params;    // effect params
    float4 maskRect;  // x0 y0 x1 y1 (frame px) the mask spans
    float4 misc;      // opacity, mode (0 composite, 1 effect), effect (1 blur, 2 pixelate, 3 colour), hasMask
};
cbuffer CB : register(b0) { Layer L; uint2 frameSize; float sigma; int radius; float4 wts[64]; };

Texture2D<uint>   srcFrame : register(t0);   // input frame (packed BGRA)
Texture2D<float4> asset    : register(t1);
Texture2D<float>  maskTex  : register(t2);
Texture2D<float4> blurred  : register(t3);   // blur result (BGRA in .rgba), valid inside the layer box
RWTexture2D<uint> work     : register(u0);
RWTexture2D<float4> blurOut : register(u1);
SamplerState lin : register(s0);

float4 unpack(uint v) { return float4(v & 255, (v >> 8) & 255, (v >> 16) & 255, v >> 24) / 255.0; }
uint pack(float3 c, uint a) {
    uint3 b = (uint3)(saturate(c) * 255.0 + 0.5);
    return b.x | (b.y << 8) | (b.z << 16) | (a & 0xFF000000u);
}

[numthreads(16, 16, 1)]
void layer_cs(uint3 id : SV_DispatchThreadID) {
    uint2 p = uint2(L.bbox.xy) + id.xy;
    if (p.x >= (uint)L.bbox.z || p.y >= (uint)L.bbox.w) return;
    float2 pos = float2(p) + 0.5;              // pixel centre
    float2 pix = float2(p);                    // pixel-index space (OpenCV convention)
    float k = L.misc.x;
    if (L.misc.w > 0.5) {
        float2 mu = (pos - L.maskRect.xy) / (L.maskRect.zw - L.maskRect.xy);
        k *= maskTex.SampleLevel(lin, mu, 0);
    }
    if (k <= 0.0) return;
    uint dv = work[p];
    float3 dst = unpack(dv).rgb;
    float3 res;
    int mode = (int)(L.misc.y + 0.5);
    if (mode == 0) {
        float2 uv = float2(L.inv.x * pix.x + L.inv.y * pix.y + L.tsz.x, L.inv.z * pix.x + L.inv.w * pix.y + L.tsz.y);
        float4 a = asset.SampleLevel(lin, (uv + 0.5) / L.tsz.zw, 0);
        float4 inner = a + L.bg * (1.0 - a.a);
        res = inner.rgb * k + dst * (1.0 - k * inner.a);
    } else {
        int effect = (int)(L.misc.z + 0.5);
        float3 c;
        if (effect == 1) {
            c = blurred.Load(int3(p, 0)).rgb;
        } else if (effect == 2) {
            float b = max(L.params.x, 1.0);
            int2 cell = int2(floor(pix / b) * b + floor(b * 0.5));
            cell = clamp(cell, int2(0, 0), int2(frameSize) - 1);
            c = unpack(srcFrame.Load(int3(cell, 0))).rgb;
        } else {
            c = unpack(srcFrame.Load(int3(p, 0))).rgb;
            c = (c - 0.5) * L.params.y + 0.5 + L.params.x;      // contrast, brightness
            float lum = dot(c, float3(0.0722, 0.7152, 0.2126)); // .rgb holds B, G, R
            c = saturate(lum + (c - lum) * L.params.z);         // saturation
        }
        res = c * k + dst * (1.0 - k);
    }
    work[p] = pack(res, dv);
}

// normalised Gaussian weights for offsets 0..radius (computed once per layer on the CPU)
float gauss_w(int i) { uint a = (uint)abs(i); return wts[a >> 2][a & 3]; }

// Separable Gaussian with group-shared tiles: each group loads its run of TILE pixels plus a `radius`
// apron once, then every thread convolves from shared memory (about one texture load per pixel instead
// of 2 * radius + 1 per pass).
#define TILE 256
groupshared float4 sh[TILE + 2 * 255];

// pass 1: horizontal, srcFrame -> blurOut (blurA), rows of the box widened by `radius` above and below
[numthreads(TILE, 1, 1)]
void blur_h_cs(uint3 gid : SV_GroupID, uint3 gt : SV_GroupThreadID) {
    int y = (int)L.bbox.y - radius + (int)gid.y;
    int x0 = (int)L.bbox.x + (int)gid.x * TILE;
    int yy = clamp(y, 0, (int)frameSize.y - 1);
    for (int i = (int)gt.x; i < TILE + 2 * radius; i += TILE) {
        int x = clamp(x0 - radius + i, 0, (int)frameSize.x - 1);   // BORDER_REPLICATE at the frame edge
        sh[i] = unpack(srcFrame.Load(int3(x, yy, 0)));
    }
    GroupMemoryBarrierWithGroupSync();
    int x = x0 + (int)gt.x;
    if (x >= (int)L.bbox.z || y < 0 || y >= (int)frameSize.y) return;
    float4 acc = 0;
    for (int k = -radius; k <= radius; ++k) acc += sh[(int)gt.x + radius + k] * gauss_w(k);
    blurOut[int2(x, y)] = acc;
}

// pass 2: vertical, hpass (blurA as SRV) -> blurOut (blurB), inside the box
Texture2D<float4> hpass : register(t4);
[numthreads(1, TILE, 1)]
void blur_v_cs(uint3 gid : SV_GroupID, uint3 gt : SV_GroupThreadID) {
    int x = (int)L.bbox.x + (int)gid.x;
    int y0 = (int)L.bbox.y + (int)gid.y * TILE;
    for (int i = (int)gt.y; i < TILE + 2 * radius; i += TILE) {
        int y = clamp(y0 - radius + i, 0, (int)frameSize.y - 1);
        sh[i] = hpass.Load(int3(x, y, 0));
    }
    GroupMemoryBarrierWithGroupSync();
    int y = y0 + (int)gt.y;
    if (y >= (int)L.bbox.w) return;
    float4 acc = 0;
    for (int k = -radius; k <= radius; ++k) acc += sh[(int)gt.y + radius + k] * gauss_w(k);
    // match the CPU path, which rounds the 8-bit blur result before blending
    blurOut[int2(x, y)] = floor(acc * 255.0 + 0.5) / 255.0;
})HLSL";

constexpr int kMaxRadius = 255;
struct CB {
    float layer[28];
    uint32_t frameW, frameH;
    float sigma;
    int32_t radius;
    float weights[kMaxRadius + 1];   // HLSL float4 wts[64]
};
static_assert(sizeof(CB) % 16 == 0, "constant buffer size");

struct Slot {
    ComPtr<ID3D11Texture2D> tex;
    ComPtr<ID3D11ShaderResourceView> srv;
    int w = 0, h = 0, fmt = 0;
};

struct Ctx {
    ComPtr<ID3D11Device> dev;
    ComPtr<ID3D11DeviceContext> ctx;
    ComPtr<ID3D11ComputeShader> layerCS, blurH, blurV;
    ComPtr<ID3D11Buffer> cb;
    ComPtr<ID3D11SamplerState> sampler;
    // frame-size resources
    int fw = 0, fh = 0;
    ComPtr<ID3D11Texture2D> srcTex, workTex, staging, blurA, blurB;
    ComPtr<ID3D11ShaderResourceView> srcSRV, blurASRV, blurBSRV;
    ComPtr<ID3D11UnorderedAccessView> workUAV, blurAUAV, blurBUAV;
    ComPtr<ID3D11Query> qDisjoint, qStart, qEnd;
    std::map<int, Slot> slots;
    std::string name, error;
    bool warp = false;
};

static void set_error(Ctx *c, const char *fmt, HRESULT hr) {
    char buf[256];
    snprintf(buf, sizeof buf, "%s (0x%08lX)", fmt, (unsigned long)hr);
    c->error = buf;
}

static bool compile(Ctx *c, const char *entry, ComPtr<ID3D11ComputeShader> &out) {
    ComPtr<ID3DBlob> code, err;
    HRESULT hr = D3DCompile(kShader, strlen(kShader), "vcs_d3d11.hlsl", nullptr, nullptr, entry, "cs_5_0",
                            D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &code, &err);
    if (FAILED(hr)) {
        c->error = std::string("shader compile failed: ") + (err ? (const char *)err->GetBufferPointer() : entry);
        return false;
    }
    hr = c->dev->CreateComputeShader(code->GetBufferPointer(), code->GetBufferSize(), nullptr, &out);
    if (FAILED(hr)) { set_error(c, "CreateComputeShader failed", hr); return false; }
    return true;
}

// Pick the hardware adapter with the most dedicated memory; fall back to WARP (software) if none works.
static bool create_device(Ctx *c, int adapterIndex) {
    D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_1, D3D_FEATURE_LEVEL_11_0};
    UINT flags = D3D11_CREATE_DEVICE_SINGLETHREADED;
    ComPtr<IDXGIFactory1> factory;
    if (SUCCEEDED(CreateDXGIFactory1(IID_PPV_ARGS(&factory)))) {
        ComPtr<IDXGIAdapter1> best;
        SIZE_T bestMem = 0;
        int bestIdx = -1;
        ComPtr<IDXGIAdapter1> a;
        for (UINT i = 0; factory->EnumAdapters1(i, &a) != DXGI_ERROR_NOT_FOUND; ++i, a.Reset()) {
            DXGI_ADAPTER_DESC1 d;
            a->GetDesc1(&d);
            if ((d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) || d.VendorId == 0x1414) continue;   // WARP / Basic Render
            if (adapterIndex >= 0 ? (int)i == adapterIndex : d.DedicatedVideoMemory > bestMem) {
                best = a; bestMem = d.DedicatedVideoMemory; bestIdx = (int)i;
            }
        }
        if (best) {
            D3D_FEATURE_LEVEL got;
            if (SUCCEEDED(D3D11CreateDevice(best.Get(), D3D_DRIVER_TYPE_UNKNOWN, nullptr, flags, levels, 2,
                                            D3D11_SDK_VERSION, &c->dev, &got, &c->ctx))) {
                DXGI_ADAPTER_DESC1 d;
                best->GetDesc1(&d);
                char n[256];
                WideCharToMultiByte(CP_UTF8, 0, d.Description, -1, n, sizeof n, nullptr, nullptr);
                c->name = n;
                (void)bestIdx;
                return true;
            }
        }
    }
    D3D_FEATURE_LEVEL got;
    HRESULT hr = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, flags, levels, 2, D3D11_SDK_VERSION,
                                   &c->dev, &got, &c->ctx);
    if (FAILED(hr)) { set_error(c, "D3D11CreateDevice failed", hr); return false; }
    c->name = "Microsoft WARP (software)";
    c->warp = true;
    return true;
}

static bool init(Ctx *c, int adapterIndex) {
    if (!create_device(c, adapterIndex)) return false;
    if (!compile(c, "layer_cs", c->layerCS) || !compile(c, "blur_h_cs", c->blurH) || !compile(c, "blur_v_cs", c->blurV))
        return false;
    D3D11_BUFFER_DESC bd = {};
    bd.ByteWidth = sizeof(CB);
    bd.Usage = D3D11_USAGE_DYNAMIC;
    bd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    bd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    HRESULT hr = c->dev->CreateBuffer(&bd, nullptr, &c->cb);
    if (FAILED(hr)) { set_error(c, "constant buffer", hr); return false; }
    D3D11_SAMPLER_DESC sd = {};
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_BORDER;   // border colour 0
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    hr = c->dev->CreateSamplerState(&sd, &c->sampler);
    if (FAILED(hr)) { set_error(c, "sampler", hr); return false; }
    D3D11_QUERY_DESC qd = {D3D11_QUERY_TIMESTAMP_DISJOINT, 0};
    c->dev->CreateQuery(&qd, &c->qDisjoint);
    qd.Query = D3D11_QUERY_TIMESTAMP;
    c->dev->CreateQuery(&qd, &c->qStart);
    c->dev->CreateQuery(&qd, &c->qEnd);
    return true;
}

static bool make_tex(Ctx *c, int w, int h, DXGI_FORMAT f, UINT bind, D3D11_USAGE usage, UINT cpu,
                     ComPtr<ID3D11Texture2D> &out) {
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = w; td.Height = h; td.MipLevels = 1; td.ArraySize = 1; td.Format = f;
    td.SampleDesc.Count = 1; td.Usage = usage; td.BindFlags = bind; td.CPUAccessFlags = cpu;
    HRESULT hr = c->dev->CreateTexture2D(&td, nullptr, &out);
    if (FAILED(hr)) { set_error(c, "CreateTexture2D failed", hr); return false; }
    return true;
}

static bool ensure_frame(Ctx *c, int w, int h) {
    if (c->fw == w && c->fh == h) return true;
    c->srcTex.Reset(); c->workTex.Reset(); c->staging.Reset(); c->blurA.Reset(); c->blurB.Reset();
    c->srcSRV.Reset(); c->blurASRV.Reset(); c->blurBSRV.Reset(); c->workUAV.Reset(); c->blurAUAV.Reset(); c->blurBUAV.Reset();
    c->fw = c->fh = 0;
    if (!make_tex(c, w, h, DXGI_FORMAT_R32_UINT, D3D11_BIND_SHADER_RESOURCE, D3D11_USAGE_DEFAULT, 0, c->srcTex) ||
        !make_tex(c, w, h, DXGI_FORMAT_R32_UINT, D3D11_BIND_UNORDERED_ACCESS, D3D11_USAGE_DEFAULT, 0, c->workTex) ||
        !make_tex(c, w, h, DXGI_FORMAT_R32_UINT, 0, D3D11_USAGE_STAGING, D3D11_CPU_ACCESS_READ, c->staging) ||
        !make_tex(c, w, h, DXGI_FORMAT_R16G16B16A16_FLOAT, D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS,
                  D3D11_USAGE_DEFAULT, 0, c->blurA) ||
        !make_tex(c, w, h, DXGI_FORMAT_R16G16B16A16_FLOAT, D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_UNORDERED_ACCESS,
                  D3D11_USAGE_DEFAULT, 0, c->blurB))
        return false;
    HRESULT hr = c->dev->CreateShaderResourceView(c->srcTex.Get(), nullptr, &c->srcSRV);
    if (SUCCEEDED(hr)) hr = c->dev->CreateUnorderedAccessView(c->workTex.Get(), nullptr, &c->workUAV);
    if (SUCCEEDED(hr)) hr = c->dev->CreateShaderResourceView(c->blurA.Get(), nullptr, &c->blurASRV);
    if (SUCCEEDED(hr)) hr = c->dev->CreateUnorderedAccessView(c->blurA.Get(), nullptr, &c->blurAUAV);
    if (SUCCEEDED(hr)) hr = c->dev->CreateShaderResourceView(c->blurB.Get(), nullptr, &c->blurBSRV);
    if (SUCCEEDED(hr)) hr = c->dev->CreateUnorderedAccessView(c->blurB.Get(), nullptr, &c->blurBUAV);
    if (FAILED(hr)) { set_error(c, "frame views", hr); return false; }
    c->fw = w; c->fh = h;
    return true;
}

static void write_cb(Ctx *c, const float *layer, float sigma, int radius) {
    D3D11_MAPPED_SUBRESOURCE m;
    if (SUCCEEDED(c->ctx->Map(c->cb.Get(), 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
        CB *cb = (CB *)m.pData;
        memcpy(cb->layer, layer, sizeof cb->layer);
        cb->frameW = c->fw; cb->frameH = c->fh; cb->sigma = sigma; cb->radius = radius;
        if (radius > 0) {                         // normalised Gaussian, as cv2.getGaussianKernel
            double sum = 0;
            for (int i = -radius; i <= radius; ++i) sum += std::exp(-(double)i * i / (2.0 * sigma * sigma));
            for (int i = 0; i <= radius; ++i) cb->weights[i] = (float)(std::exp(-(double)i * i / (2.0 * sigma * sigma)) / sum);
        }
        c->ctx->Unmap(c->cb.Get(), 0);
    }
}

static void unbind(Ctx *c) {
    ID3D11ShaderResourceView *nullSRV[5] = {};
    ID3D11UnorderedAccessView *nullUAV[2] = {};
    c->ctx->CSSetShaderResources(0, 5, nullSRV);
    c->ctx->CSSetUnorderedAccessViews(0, 2, nullUAV, nullptr);
}

static UINT groups(float a, float b) { return (UINT)std::max(0.0f, std::ceil((b - a) / 16.0f)); }

extern "C" {

__declspec(dllexport) void *vcs_create(int adapterIndex, int *status) {
    Ctx *c = new Ctx();
    if (!init(c, adapterIndex)) {
        if (status) *status = -1;
        OutputDebugStringA(c->error.c_str());
        static std::string lastCreateError;
        lastCreateError = c->error;
        delete c;
        return nullptr;
    }
    if (status) *status = c->warp ? 1 : 0;
    return c;
}

__declspec(dllexport) void vcs_destroy(void *h) { delete (Ctx *)h; }

__declspec(dllexport) void vcs_device_name(void *h, char *buf, int n) {
    Ctx *c = (Ctx *)h;
    snprintf(buf, n, "%s", c ? c->name.c_str() : "");
}

// S_OK while the device works; otherwise why it was lost (DXGI_ERROR_DEVICE_REMOVED / _RESET / _HUNG, ...): a
// driver update or crash (TDR), a laptop switching GPUs, a remote session reconnect. A lost device never comes back:
// the caller creates a new context (and uploads its textures again).
__declspec(dllexport) long vcs_device_status(void *h) {
    Ctx *c = (Ctx *)h;
    if (!c || !c->dev) return (long)E_POINTER;
    return (long)c->dev->GetDeviceRemovedReason();
}

__declspec(dllexport) void vcs_last_error(void *h, char *buf, int n) {
    Ctx *c = (Ctx *)h;
    snprintf(buf, n, "%s", c ? c->error.c_str() : "");
}

// fmt 0: (h, w, 4) BGRA uint8 -> R8G8B8A8_UNORM;  fmt 1: (h, w) uint8 mask -> R8_UNORM
__declspec(dllexport) int vcs_upload(void *h, int slot, const uint8_t *data, int w, int hgt, int stride, int fmt) {
    Ctx *c = (Ctx *)h;
    if (!c || !data || w <= 0 || hgt <= 0) return -1;
    DXGI_FORMAT f = fmt == 1 ? DXGI_FORMAT_R8_UNORM : DXGI_FORMAT_R8G8B8A8_UNORM;
    Slot &s = c->slots[slot];
    if (!s.tex || s.w != w || s.h != hgt || s.fmt != fmt) {
        s = Slot();
        D3D11_TEXTURE2D_DESC td = {};
        td.Width = w; td.Height = hgt; td.MipLevels = 1; td.ArraySize = 1; td.Format = f;
        td.SampleDesc.Count = 1; td.Usage = D3D11_USAGE_DEFAULT; td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
        HRESULT hr = c->dev->CreateTexture2D(&td, nullptr, &s.tex);
        if (FAILED(hr)) { set_error(c, "asset texture", hr); return -2; }
        hr = c->dev->CreateShaderResourceView(s.tex.Get(), nullptr, &s.srv);
        if (FAILED(hr)) { set_error(c, "asset view", hr); return -3; }
        s.w = w; s.h = hgt; s.fmt = fmt;
    }
    c->ctx->UpdateSubresource(s.tex.Get(), 0, nullptr, data, stride, 0);
    return 0;
}

__declspec(dllexport) void vcs_release_slot(void *h, int slot) {
    Ctx *c = (Ctx *)h;
    if (c) c->slots.erase(slot);
}

// src/dst: BGRA uint8 frames (row stride in bytes); layers: n x 28 floats; slots: n x 2 ints (asset, mask; -1 none)
// timing[0] = CPU wall ms of the call, timing[1] = GPU ms (timestamp queries; 0 if unavailable)
__declspec(dllexport) int vcs_render(void *h, const uint8_t *src, int w, int hgt, int srcStride, uint8_t *dst,
                                     int dstStride, const float *layers, const int *slots, int n, double *timing) {
    Ctx *c = (Ctx *)h;
    if (!c || !src || !dst) return -1;
    auto t0 = std::chrono::steady_clock::now();
    if (!ensure_frame(c, w, hgt)) return -2;
    bool timed = c->qDisjoint && c->qStart && c->qEnd;
    if (timed) { c->ctx->Begin(c->qDisjoint.Get()); c->ctx->End(c->qStart.Get()); }
    c->ctx->UpdateSubresource(c->srcTex.Get(), 0, nullptr, src, srcStride, 0);
    c->ctx->CopyResource(c->workTex.Get(), c->srcTex.Get());
    ID3D11SamplerState *smp = c->sampler.Get();
    ID3D11Buffer *cb = c->cb.Get();
    c->ctx->CSSetSamplers(0, 1, &smp);
    c->ctx->CSSetConstantBuffers(0, 1, &cb);
    for (int i = 0; i < n; ++i) {
        const float *L = layers + i * 28;
        float x0 = L[0], y0 = L[1], x1 = L[2], y1 = L[3];
        if (x1 <= x0 || y1 <= y0) continue;
        int mode = (int)(L[25] + 0.5f), effect = (int)(L[26] + 0.5f);
        ID3D11ShaderResourceView *blurSRV = nullptr;
        if (mode == 1 && effect == 1) {        // Gaussian blur of the input inside the box
            float sigma = std::max(0.5f, L[16]);
            int ksize = ((int)std::lround(sigma * 6 + 1)) | 1;    // cv2.GaussianBlur, 8-bit images
            int radius = std::min(ksize / 2, kMaxRadius);
            write_cb(c, L, sigma, radius);
            unbind(c);
            ID3D11ShaderResourceView *s0 = c->srcSRV.Get();
            ID3D11UnorderedAccessView *ua = c->blurAUAV.Get();
            c->ctx->CSSetShaderResources(0, 1, &s0);
            c->ctx->CSSetUnorderedAccessViews(1, 1, &ua, nullptr);
            c->ctx->CSSetShader(c->blurH.Get(), nullptr, 0);
            c->ctx->Dispatch((UINT)std::ceil((x1 - x0) / 256.0f), (UINT)(y1 - y0) + 2 * radius, 1);
            unbind(c);
            ID3D11ShaderResourceView *hp = c->blurASRV.Get();
            ID3D11UnorderedAccessView *ub = c->blurBUAV.Get();
            c->ctx->CSSetShaderResources(4, 1, &hp);
            c->ctx->CSSetUnorderedAccessViews(1, 1, &ub, nullptr);
            c->ctx->CSSetShader(c->blurV.Get(), nullptr, 0);
            c->ctx->Dispatch((UINT)(x1 - x0), (UINT)std::ceil((y1 - y0) / 256.0f), 1);
            unbind(c);
            blurSRV = c->blurBSRV.Get();
        } else {
            write_cb(c, L, 0.0f, 0);
        }
        auto find = [&](int idx) -> ID3D11ShaderResourceView * {
            if (idx < 0) return nullptr;
            auto it = c->slots.find(idx);
            return it == c->slots.end() ? nullptr : it->second.srv.Get();
        };
        ID3D11ShaderResourceView *srvs[4] = {c->srcSRV.Get(), find(slots[i * 2]), find(slots[i * 2 + 1]), blurSRV};
        if (mode == 0 && !srvs[1]) { c->error = "asset texture slot missing"; unbind(c); return -3; }
        if (L[27] > 0.5f && !srvs[2]) { c->error = "mask texture slot missing"; unbind(c); return -4; }
        ID3D11UnorderedAccessView *wu = c->workUAV.Get();
        c->ctx->CSSetShaderResources(0, 4, srvs);
        c->ctx->CSSetUnorderedAccessViews(0, 1, &wu, nullptr);
        c->ctx->CSSetShader(c->layerCS.Get(), nullptr, 0);
        c->ctx->Dispatch(groups(x0, x1), groups(y0, y1), 1);
        unbind(c);
    }
    c->ctx->CopyResource(c->staging.Get(), c->workTex.Get());
    if (timed) { c->ctx->End(c->qEnd.Get()); c->ctx->End(c->qDisjoint.Get()); }
    D3D11_MAPPED_SUBRESOURCE m;
    HRESULT hr = c->ctx->Map(c->staging.Get(), 0, D3D11_MAP_READ, 0, &m);
    if (FAILED(hr)) { set_error(c, "readback Map failed", hr); return -5; }
    const uint8_t *s = (const uint8_t *)m.pData;
    for (int y = 0; y < hgt; ++y) memcpy(dst + (size_t)y * dstStride, s + (size_t)y * m.RowPitch, (size_t)w * 4);
    c->ctx->Unmap(c->staging.Get(), 0);
    double gpu = 0;
    if (timed) {
        D3D11_QUERY_DATA_TIMESTAMP_DISJOINT dj;
        UINT64 a = 0, b = 0;
        if (c->ctx->GetData(c->qDisjoint.Get(), &dj, sizeof dj, 0) == S_OK && !dj.Disjoint &&
            c->ctx->GetData(c->qStart.Get(), &a, sizeof a, 0) == S_OK &&
            c->ctx->GetData(c->qEnd.Get(), &b, sizeof b, 0) == S_OK && dj.Frequency)
            gpu = double(b - a) * 1000.0 / double(dj.Frequency);
    }
    if (timing) {
        timing[0] = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
        timing[1] = gpu;
    }
    return 0;
}

__declspec(dllexport) void vcs_flush(void *h, int dropSlots) {
    Ctx *c = (Ctx *)h;
    if (!c) return;
    if (dropSlots) c->slots.clear();
    c->ctx->Flush();
}

__declspec(dllexport) int vcs_is_warp(void *h) { return h && ((Ctx *)h)->warp ? 1 : 0; }

}  // extern "C"
