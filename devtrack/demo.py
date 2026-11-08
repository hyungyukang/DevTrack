"""예시 데이터 생성 — 화면 확인용.

실제 git 저장소(<data>/.repos/cfd-solver)를 만들어 커밋 승인 요청의 diff 가 진짜이고,
웹에서 승인한 뒤 ``devtrack --data <dir> apply T-0001 A-1`` 로 실제 커밋까지 해볼 수 있다.
"""

import datetime as _dt
import json
import math
import os
import random
import shutil
import subprocess
import tempfile

from . import DevTrackError
from . import store as store_mod


class _Clock:
    def __init__(self):
        self.base = _dt.datetime.now()
        self.t = self.base

    def __call__(self):
        return self.t

    def at(self, minutes_ago):
        self.t = self.base - _dt.timedelta(minutes=minutes_ago)


# ------------------------------------------------------------------ SVG / PDF 헬퍼
_PALETTE = ["#3a5ccc", "#e8590c", "#2b8a3e", "#ae3ec9"]


def svg_line_chart(series, title, xlabel, ylabel, logy=False, w=640, h=380):
    pad_l, pad_r, pad_t, pad_b = 64, 20, 44, 52
    xs = [x for _, pts in series for x, _ in pts]
    ys = [y for _, pts in series for _, y in pts]
    tf = (lambda v: math.log10(v)) if logy else (lambda v: v)
    x0, x1 = min(xs), max(xs)
    y0, y1 = tf(min(ys)), tf(max(ys))
    if logy:
        y0, y1 = math.floor(y0), math.ceil(y1)
    else:
        span = (y1 - y0) or 1
        y0, y1 = y0 - span * 0.05, y1 + span * 0.08
    px = lambda x: pad_l + (x - x0) / ((x1 - x0) or 1) * (w - pad_l - pad_r)
    py = lambda y: h - pad_b - (tf(y) - y0) / ((y1 - y0) or 1) * (h - pad_t - pad_b)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
           'font-family="Helvetica, Arial, sans-serif">'.format(w=w, h=h),
           '<rect width="100%" height="100%" fill="#fff"/>',
           '<text x="{}" y="26" font-size="15" font-weight="bold" fill="#1a1e24">{}</text>'.format(pad_l, title)]
    ticks = range(int(y0), int(y1) + 1) if logy else [y0 + (y1 - y0) * i / 5 for i in range(6)]
    for tv in ticks:
        yy = h - pad_b - (tv - y0) / ((y1 - y0) or 1) * (h - pad_t - pad_b)
        label = "1e{}".format(tv) if logy else "{:.3g}".format(tv)
        out.append('<line x1="{}" x2="{}" y1="{:.1f}" y2="{:.1f}" stroke="#e5e7eb"/>'.format(pad_l, w - pad_r, yy, yy))
        out.append('<text x="{}" y="{:.1f}" font-size="11" fill="#6b7280" text-anchor="end">{}</text>'.format(pad_l - 8, yy + 4, label))
    for i in range(6):
        xv = x0 + (x1 - x0) * i / 5
        out.append('<text x="{:.1f}" y="{}" font-size="11" fill="#6b7280" text-anchor="middle">{:.3g}</text>'.format(px(xv), h - pad_b + 18, xv))
    out.append('<line x1="{0}" x2="{1}" y1="{2}" y2="{2}" stroke="#9ca3af"/>'.format(pad_l, w - pad_r, h - pad_b))
    for i, (name, pts) in enumerate(series):
        c = _PALETTE[i % len(_PALETTE)]
        d = " ".join("{}{:.1f},{:.1f}".format("M" if j == 0 else "L", px(x), py(y)) for j, (x, y) in enumerate(pts))
        out.append('<path d="{}" fill="none" stroke="{}" stroke-width="2"/>'.format(d, c))
        out.append('<rect x="{}" y="{}" width="12" height="3" fill="{}"/>'.format(w - pad_r - 150, pad_t + 4 + i * 18, c))
        out.append('<text x="{}" y="{}" font-size="12" fill="#374151">{}</text>'.format(w - pad_r - 132, pad_t + 9 + i * 18, name))
    out.append('<text x="{}" y="{}" font-size="12" fill="#374151" text-anchor="middle">{}</text>'.format((pad_l + w - pad_r) / 2, h - 12, xlabel))
    out.append('<text transform="translate(16 {}) rotate(-90)" font-size="12" fill="#374151" text-anchor="middle">{}</text>'.format((pad_t + h - pad_b) / 2, ylabel))
    out.append("</svg>")
    return "\n".join(out)


def svg_bar_chart(cats, vals, title, ylabel, w=640, h=360, highlight=None):
    pad_l, pad_r, pad_t, pad_b = 64, 20, 44, 56
    vmax = max(vals) * 1.15
    bw = (w - pad_l - pad_r) / len(cats)
    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
           'font-family="Helvetica, Arial, sans-serif">'.format(w=w, h=h),
           '<rect width="100%" height="100%" fill="#fff"/>',
           '<text x="{}" y="26" font-size="15" font-weight="bold" fill="#1a1e24">{}</text>'.format(pad_l, title)]
    for i in range(5):
        v = vmax * i / 4
        y = h - pad_b - v / vmax * (h - pad_t - pad_b)
        out.append('<line x1="{}" x2="{}" y1="{:.1f}" y2="{:.1f}" stroke="#e5e7eb"/>'.format(pad_l, w - pad_r, y, y))
        out.append('<text x="{}" y="{:.1f}" font-size="11" fill="#6b7280" text-anchor="end">{:.3g}</text>'.format(pad_l - 8, y + 4, v))
    for i, (c, v) in enumerate(zip(cats, vals)):
        x = pad_l + i * bw + bw * 0.18
        bh = v / vmax * (h - pad_t - pad_b)
        col = _PALETTE[1] if highlight == i else _PALETTE[0]
        out.append('<rect x="{:.1f}" y="{:.1f}" width="{:.1f}" height="{:.1f}" rx="3" fill="{}"/>'.format(x, h - pad_b - bh, bw * 0.64, bh, col))
        out.append('<text x="{:.1f}" y="{:.1f}" font-size="12" fill="#111827" text-anchor="middle" font-weight="bold">{:.3g}</text>'.format(x + bw * 0.32, h - pad_b - bh - 6, v))
        out.append('<text x="{:.1f}" y="{}" font-size="11" fill="#374151" text-anchor="middle">{}</text>'.format(x + bw * 0.32, h - pad_b + 18, c))
    out.append('<line x1="{0}" x2="{1}" y1="{2}" y2="{2}" stroke="#9ca3af"/>'.format(pad_l, w - pad_r, h - pad_b))
    out.append('<text transform="translate(16 {}) rotate(-90)" font-size="12" fill="#374151" text-anchor="middle">{}</text>'.format((pad_t + h - pad_b) / 2, ylabel))
    out.append("</svg>")
    return "\n".join(out)


def simple_pdf(title, lines):
    """ASCII 텍스트 한 페이지짜리 PDF."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    parts = ["BT /F2 18 Tf 56 780 Td ({}) Tj ET".format(esc(title)),
             "0.8 0.8 0.8 RG 56 768 m 539 768 l S"]
    y = 744
    for line in lines:
        bold = line.startswith("## ")
        text = line[3:] if bold else line
        size = 13 if bold else 10.5
        if bold:
            y -= 6
        parts.append("BT /{} {} Tf 56 {} Td ({}) Tj ET".format("F2" if bold else "F1", size, y, esc(text)))
        y -= 20 if bold else 15
    stream = "\n".join(parts).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += "{} 0 obj\n".format(i).encode() + o + b"\nendobj\n"
    xref = len(out)
    out += "xref\n0 {}\n0000000000 65535 f \n".format(len(objs) + 1).encode()
    for off in offsets:
        out += "{:010d} 00000 n \n".format(off).encode()
    out += "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n".format(len(objs) + 1, xref).encode()
    return bytes(out)


# ------------------------------------------------------------------ 데모용 git 저장소
SOLVER_V1 = """#include <cuda_runtime.h>
#include "solver.h"

// Jacobi iteration for the pressure Poisson equation
__global__ void jacobi_kernel(const double* p, double* p_new, const double* rhs,
                              int nx, int ny, double dx2) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  int j = blockIdx.y * blockDim.y + threadIdx.y;
  if (i <= 0 || j <= 0 || i >= nx - 1 || j >= ny - 1) return;
  int k = j * nx + i;
  p_new[k] = 0.25 * (p[k - 1] + p[k + 1] + p[k - nx] + p[k + nx] - dx2 * rhs[k]);
}

void solve_pressure(Field& f, int iters) {
  double *d_p, *d_new, *d_rhs;
  size_t bytes = f.nx * f.ny * sizeof(double);
  cudaMalloc(&d_p, bytes);
  cudaMalloc(&d_new, bytes);
  cudaMalloc(&d_rhs, bytes);
  cudaMemcpy(d_p, f.p, bytes, cudaMemcpyHostToDevice);
  cudaMemcpy(d_rhs, f.rhs, bytes, cudaMemcpyHostToDevice);
  dim3 block(16, 16);
  dim3 grid((f.nx + 15) / 16, (f.ny + 15) / 16);
  for (int it = 0; it < iters; ++it) {
    jacobi_kernel<<<grid, block>>>(d_p, d_new, d_rhs, f.nx, f.ny, f.dx * f.dx);
    std::swap(d_p, d_new);
  }
  cudaMemcpy(f.p, d_p, bytes, cudaMemcpyDeviceToHost);
  cudaFree(d_p);
  cudaFree(d_new);
  cudaFree(d_rhs);
}
"""

SOLVER_V2 = """#include <hip/hip_runtime.h>
#include "hip_utils.h"
#include "solver.h"

// Jacobi iteration for the pressure Poisson equation (HIP port)
__global__ void jacobi_kernel(const double* __restrict__ p, double* __restrict__ p_new,
                              const double* __restrict__ rhs, int nx, int ny, double dx2) {
  int i = blockIdx.x * blockDim.x + threadIdx.x;
  int j = blockIdx.y * blockDim.y + threadIdx.y;
  if (i <= 0 || j <= 0 || i >= nx - 1 || j >= ny - 1) return;
  int k = j * nx + i;
  p_new[k] = 0.25 * (p[k - 1] + p[k + 1] + p[k - nx] + p[k + nx] - dx2 * rhs[k]);
}

void solve_pressure(Field& f, int iters, hipStream_t stream) {
  double *d_p, *d_new, *d_rhs;
  size_t bytes = f.nx * f.ny * sizeof(double);
  HIP_CHECK(hipMalloc(&d_p, bytes));
  HIP_CHECK(hipMalloc(&d_new, bytes));
  HIP_CHECK(hipMalloc(&d_rhs, bytes));
  HIP_CHECK(hipMemcpyAsync(d_p, f.p, bytes, hipMemcpyHostToDevice, stream));
  HIP_CHECK(hipMemcpyAsync(d_rhs, f.rhs, bytes, hipMemcpyHostToDevice, stream));
  // MI250X: 64-wide wavefronts -> 64x4 blocks
  dim3 block(64, 4);
  dim3 grid((f.nx + 63) / 64, (f.ny + 3) / 4);
  for (int it = 0; it < iters; ++it) {
    hipLaunchKernelGGL(jacobi_kernel, grid, block, 0, stream, d_p, d_new, d_rhs, f.nx, f.ny, f.dx * f.dx);
    std::swap(d_p, d_new);
  }
  HIP_CHECK(hipMemcpyAsync(f.p, d_p, bytes, hipMemcpyDeviceToHost, stream));
  HIP_CHECK(hipStreamSynchronize(stream));
  HIP_CHECK(hipFree(d_p));
  HIP_CHECK(hipFree(d_new));
  HIP_CHECK(hipFree(d_rhs));
}
"""

HIP_UTILS = """#pragma once
#include <cstdio>
#include <cstdlib>
#include <hip/hip_runtime.h>

#define HIP_CHECK(cmd)                                                        \\
  do {                                                                        \\
    hipError_t e = (cmd);                                                     \\
    if (e != hipSuccess) {                                                    \\
      std::fprintf(stderr, "HIP error %s at %s:%d\\n", hipGetErrorString(e),  \\
                   __FILE__, __LINE__);                                       \\
      std::exit(EXIT_FAILURE);                                                \\
    }                                                                         \\
  } while (0)
"""

CMAKE_V1 = """cmake_minimum_required(VERSION 3.21)
project(cfd_solver LANGUAGES CXX CUDA)
set(CMAKE_CXX_STANDARD 17)
add_executable(cfd src/main.cpp src/pressure_solver.cu)
target_include_directories(cfd PRIVATE include)
"""

CMAKE_V2 = """cmake_minimum_required(VERSION 3.21)
project(cfd_solver LANGUAGES CXX HIP)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_HIP_ARCHITECTURES gfx90a)
add_executable(cfd src/main.cpp src/pressure_solver.hip)
target_include_directories(cfd PRIVATE include)
"""

TEST_FILE = '''import numpy as np
import pytest

from cfd_solver import solve_pressure_cpu


@pytest.mark.parametrize("n", [32, 64])
def test_jacobi_converges_on_manufactured_solution(n):
    x = np.linspace(0, 1, n)
    X, Y = np.meshgrid(x, x)
    exact = np.sin(np.pi * X) * np.sin(np.pi * Y)
    rhs = -2 * np.pi ** 2 * exact
    p = solve_pressure_cpu(rhs, dx=x[1] - x[0], iters=20000)
    assert np.max(np.abs(p - exact)) < 5e-3
'''

CI_FILE = """name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.11"}
      - run: pip install -e .[test]
      - run: pytest -q
"""


def _git(repo, *args):
    subprocess.run(["git", "-C", repo] + list(args), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_repo(root):
    repo = os.path.join(root, ".repos", "cfd-solver")
    os.makedirs(repo)
    _git(repo, "init", "-q")
    _git(repo, "checkout", "-q", "-b", "feature/hip-pressure")
    _git(repo, "config", "user.name", "DevTrack Demo")
    _git(repo, "config", "user.email", "demo@devtrack.local")
    _write(os.path.join(repo, "src", "pressure_solver.cu"), SOLVER_V1)
    _write(os.path.join(repo, "CMakeLists.txt"), CMAKE_V1)
    _write(os.path.join(repo, "src", "main.cpp"), '#include "solver.h"\nint main() { return run(); }\n')
    _write(os.path.join(repo, "README.md"), "# cfd-solver\n\n2D incompressible Navier-Stokes solver.\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial CUDA implementation")
    return repo


# ------------------------------------------------------------------ 데모 시나리오
def build_demo(store, force=False):
    root = store.root
    if os.path.exists(os.path.join(root, "tasks")) and store.task_ids():
        if not force:
            raise DevTrackError("데이터 디렉토리에 이미 작업이 있습니다: {} (--force 로 덮어쓰기)".format(root))
        for name in ("tasks", ".repos", "index.json"):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.exists(p):
                os.unlink(p)
    os.makedirs(root, exist_ok=True)
    clk = _Clock()
    store_mod._clock = clk
    tmp = tempfile.mkdtemp(prefix="devtrack-demo-")
    try:
        _scenario(store, clk, tmp)
    finally:
        store_mod._clock = None
        shutil.rmtree(tmp, ignore_errors=True)
    store.rebuild_index()


def _scenario(s, clk, tmp):
    def f(name, content, mode="w"):
        path = os.path.join(tmp, name)
        with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
            fh.write(content)
        return path

    try:
        repo = _make_repo(s.root)
    except (OSError, subprocess.CalledProcessError):
        repo = None  # git 이 없으면 커밋 승인 예시는 생략

    # ---------------------------------------------------------------- T-0001 CI 테스트 추가 (완료, 커밋됨)
    clk.at(2900)
    t1 = s.create_task(
        "CI 파이프라인에 압력 솔버 단위 테스트 추가", type="code", agent="Claude", repo=repo,
        instruction="압력 솔버(CPU 참조 구현)에 대해 **manufactured solution** 기반 단위 테스트를 추가하고 "
                    "GitHub Actions 에서 자동 실행되도록 해주세요.\n\n- 격자 32, 64 두 경우\n- 허용 오차 5e-3",
        plan=["기존 테스트 구조 확인", "manufactured solution 테스트 작성", "CI 워크플로 추가", "커밋 승인 요청"],
        tags=["ci", "test"])
    tid1 = t1["id"]
    clk.at(2880); s.update_task(tid1, step="기존 테스트 구조 확인 — tests/ 디렉토리가 없어 새로 생성 예정", progress=10)
    clk.at(2870); s.check_plan(tid1, [1])
    clk.at(2840); s.update_task(tid1, step="sin(πx)sin(πy) 해석해로 테스트 작성")
    clk.at(2825); s.check_plan(tid1, [2])
    clk.at(2815); s.check_plan(tid1, [3])
    if repo:
        _write(os.path.join(repo, "tests", "test_pressure.py"), TEST_FILE)
        _write(os.path.join(repo, ".github", "workflows", "tests.yml"), CI_FILE)
        clk.at(2810)
        ap = s.request_approval(tid1, "테스트 및 CI 워크플로 커밋", description="로컬에서 `pytest -q` 2개 테스트 통과 확인했습니다.",
                                commit={"repo": repo, "message": "Add manufactured-solution tests and CI workflow",
                                        "push": False})
        clk.at(2760)
        s.decide(tid1, ap["id"], "approve", comment="좋습니다. 커밋해 주세요.")
        clk.at(2755)
        s.apply_approval(tid1, ap["id"], author="Claude")
    clk.at(2750); s.check_plan(tid1, [4])
    clk.at(2748); s.update_task(tid1, status="done", message="테스트 2개 추가, CI 워크플로 등록 완료")

    # ---------------------------------------------------------------- T-0002 풍동 데이터 분석 (완료)
    clk.at(1500)
    t2 = s.create_task(
        "풍동 실험 데이터 통계 분석 (받음각별 양항력)", type="analysis", agent="Claude",
        instruction="`wind_tunnel_2024.csv` 의 40개 런에 대해 받음각별 양력/항력 계수 통계를 내고, "
                    "이상치가 있으면 표시해 주세요. 결과는 노트북과 요약 리포트로 정리.",
        plan=["데이터 로드 및 정제", "기술 통계", "이상치 탐지 (IQR)", "그림 및 요약 리포트"], tags=["실험", "통계"])
    tid2 = t2["id"]
    rng = random.Random(42)
    rows = ["run_id,velocity_mps,aoa_deg,cl,cd,reynolds"]
    for i in range(40):
        aoa = [0, 2, 4, 6, 8, 10, 12, 14][i % 8]
        v = 20 + 5 * (i // 8)
        cl = 0.11 * aoa + rng.gauss(0, 0.03) + (0.5 if i == 29 else 0)
        cd = 0.012 + 0.0009 * aoa ** 2 / 4 + rng.gauss(0, 0.001)
        rows.append("R{:03d},{},{},{:.4f},{:.5f},{:.3e}".format(i + 1, v, aoa, cl, cd, v * 0.3 / 1.5e-5))
    csv_text = "\n".join(rows) + "\n"
    clk.at(1490); s.update_task(tid2, step="CSV 로드 및 단위 확인 (40 runs × 6 columns)", progress=10)
    clk.at(1480); s.check_plan(tid2, [1])
    clk.at(1460); s.check_plan(tid2, [2])
    clk.at(1440); s.add_issue(tid2, "R030 양력계수 이상치 (CL=1.33, 기대값 ≈0.77)", severity="medium",
                              detail="동일 받음각(10°) 다른 런 대비 +0.56. 센서 기록 오류 가능성.")
    clk.at(1430); s.check_plan(tid2, [3])
    aoas = [0, 2, 4, 6, 8, 10, 12, 14]
    box = svg_bar_chart(["{}°".format(a) for a in aoas], [round(0.11 * a + 0.002, 3) for a in aoas],
                        "Mean lift coefficient by angle of attack", "C_L", highlight=5)
    clk.at(1420)
    s.add_artifact(tid2, path=f("wind_tunnel_summary.csv", csv_text), title="정제된 실험 데이터 (CSV)")
    s.add_artifact(tid2, path=f("cl_by_aoa.svg", box), title="받음각별 평균 양력계수",
                   description="주황색 막대: 이상치(R030)가 포함된 10° 구간")
    nb = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "language": "python"}},
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# 풍동 실험 데이터 분석\n", "받음각별 양력/항력 계수 통계"]},
            {"cell_type": "code", "execution_count": 1, "metadata": {}, "outputs": [],
             "source": ["import pandas as pd\n", "df = pd.read_csv('wind_tunnel_2024.csv')\n", "df.shape"]},
            {"cell_type": "code", "execution_count": 2, "metadata": {},
             "source": ["df.groupby('aoa_deg')[['cl', 'cd']].mean().round(4)"],
             "outputs": [{"output_type": "execute_result", "execution_count": 2, "metadata": {},
                          "data": {"text/plain": ["         cl       cd\n", "aoa_deg                \n",
                                                  "0    0.0021  0.01198\n", "2    0.2213  0.01291\n",
                                                  "4    0.4398  0.01557\n", "10   1.0995  0.03445\n", "..."]}}]},
            {"cell_type": "code", "execution_count": 3, "metadata": {},
             "source": ["ax = df.groupby('aoa_deg').cl.mean().plot.bar()\n", "ax.set_ylabel('C_L')"],
             "outputs": [{"output_type": "display_data", "metadata": {}, "data": {"image/svg+xml": box.splitlines(True)}}]},
        ],
    }
    s.add_artifact(tid2, path=f("analysis.ipynb", json.dumps(nb, ensure_ascii=False, indent=1)), title="분석 노트북")
    clk.at(1400)
    rep = s.add_artifact(tid2, path=f("summary.md", (
        "# 풍동 실험 통계 요약\n\n| 받음각 | 평균 CL | 평균 CD | L/D |\n|---:|---:|---:|---:|\n"
        "| 0° | 0.002 | 0.0120 | 0.2 |\n| 4° | 0.440 | 0.0156 | 28.2 |\n| 8° | 0.881 | 0.0264 | 33.4 |\n"
        "| 12° | 1.322 | 0.0444 | 29.8 |\n\n## 주요 결과\n- 최대 양항비는 **8°** 부근 (L/D ≈ 33)\n"
        "- R030 은 IQR 기준 이상치 → 통계에서 제외하고 별도 표기\n- 레이놀즈 수 영향은 측정 범위 내에서 2% 미만\n")),
        title="통계 요약 리포트")
    clk.at(1395)
    ap2 = s.request_approval(tid2, "분석 결과 확인", kind="result", artifact_ids=[rep["id"]],
                             description="이상치 R030 을 제외한 통계입니다. 제외 처리가 괜찮은지 확인 부탁드립니다.")
    clk.at(1330); s.decide(tid2, ap2["id"], "approve", comment="R030 제외 동의합니다. 실험 노트에도 표시해 주세요.")
    s.ack_decision(tid2, ap2["id"])
    clk.at(1325); s.resolve_issue(tid2, "I-1", note="통계에서 제외, 리포트에 각주로 표시")
    clk.at(1320); s.check_plan(tid2, [4])
    clk.at(1318); s.update_task(tid2, status="done", message="노트북, 요약 리포트, 그림 전달 완료")

    # ---------------------------------------------------------------- T-0003 Transformer 서로게이트 (막힘)
    clk.at(380)
    t3 = s.create_task(
        "Transformer 서로게이트 모델 학습 (Frontier 8 GCD)", type="model", agent="Claude", priority="high",
        instruction="CFD 스냅샷 1,200개로 압력장 예측 Transformer 를 학습해 주세요.\n\n"
                    "- 노드 1개 (MI250X 8 GCD), PyTorch DDP\n- 목표: 검증 상대 L2 오차 < 3%\n- 체크포인트는 `/lustre/orion/proj/ckpt` 에 저장",
        plan=["데이터 로더 구성", "모델/학습 스크립트 작성", "소규모 디버그 학습", "본 학습 (200 epoch)", "평가 및 리포트"],
        tags=["pytorch", "frontier"])
    tid3 = t3["id"]
    clk.at(370); s.update_task(tid3, step="HDF5 스냅샷 로더 구성 (1,200 × 256×256)", progress=5)
    clk.at(340); s.check_plan(tid3, [1])
    clk.at(300); s.check_plan(tid3, [2])
    loss = [(e, 0.9 * math.exp(-e / 6) + 0.05 + 0.01 * math.sin(e)) for e in range(1, 31)]
    val = [(e, 0.95 * math.exp(-e / 6.5) + 0.07) for e in range(1, 31)]
    clk.at(240)
    s.add_artifact(tid3, path=f("debug_loss.svg", svg_line_chart([("train", loss), ("val", val)],
                                                                 "Debug run: loss (batch 16, 30 epochs)", "epoch", "MSE loss", logy=True)),
                   title="디버그 학습 손실 곡선")
    s.check_plan(tid3, [3])
    clk.at(200); s.update_task(tid3, step="본 학습 시작: batch 64/GCD, bf16, 200 epoch (sbatch job 2318842)")
    clk.at(150)
    s.add_issue(tid3, "본 학습 OOM — batch 64/GCD 에서 HIP out of memory", severity="critical", detail=(
        "```\ntorch.OutOfMemoryError: HIP out of memory. Tried to allocate 12.00 GiB.\n"
        "GPU 3 has a total capacity of 63.98 GiB of which 9.81 GiB is free.\n```\n"
        "어텐션 행렬(256×256 패치 → 4096 토큰)이 메모리를 대부분 차지합니다."))
    s.add_artifact(tid3, path=f("train_2318842.log", "\n".join(
        ["[rank{}] epoch 1 step {:4d} loss {:.4f}".format(r, st, 0.9 - st * 1e-4) for st in range(0, 400, 50) for r in (0,)]
        + ["Traceback (most recent call last):", '  File "train.py", line 212, in <module>',
           "torch.OutOfMemoryError: HIP out of memory. Tried to allocate 12.00 GiB."]) + "\n"),
        title="학습 로그 (job 2318842)")
    clk.at(140)
    s.add_log(tid3, "해결 방안 검토: ① batch 32 + grad accumulation 2 ② gradient checkpointing ③ 패치 크기 16→32 (토큰 1/4)",
              kind="warning", detail="①은 학습 시간 +5%, ②는 +25~30%, ③은 정확도 저하 위험이 있습니다. "
                                     "어떤 방향으로 진행할지 결정 부탁드립니다.")
    clk.at(135)
    s.update_task(tid3, status="blocked", step="OOM 해결 방향 사용자 결정 대기 — 메시지로 선택지를 알려주세요")

    # ---------------------------------------------------------------- T-0004 PR 코드 리뷰 (승인 대기)
    clk.at(300)
    t4 = s.create_task(
        "PR #42 코드 리뷰: 경계조건 모듈 리팩터링", type="review", agent="Claude",
        repo_url="https://github.com/example/cfd-solver/pull/42",
        instruction="https://github.com/example/cfd-solver/pull/42 를 리뷰해 주세요. 정확성 버그 위주로, "
                    "리포트는 markdown 과 PDF 로 남기고 PR 에 코멘트 게시 전에 승인 받으세요.",
        plan=["PR 변경사항 파악", "정확성 검토", "리뷰 리포트 작성", "PR 코멘트 게시 승인 요청"], tags=["PR-42"])
    tid4 = t4["id"]
    clk.at(290); s.update_task(tid4, step="변경 파일 14개, +612 −430 확인 — boundary.cpp 중심", progress=15)
    clk.at(280); s.check_plan(tid4, [1])
    clk.at(220); s.update_task(tid4, step="주기 경계조건 인덱스 계산 검토 중")
    clk.at(200); s.add_issue(tid4, "주기 경계에서 ghost cell 인덱스 off-by-one (nx-1 대신 nx-2 를 복사)",
                             severity="high", detail="`apply_periodic()` L88: `p[0] = p[nx-2]` 이어야 하는데 `p[nx-1]` 로 바뀜.")
    clk.at(170); s.check_plan(tid4, [2])
    review_md = """# 코드 리뷰 리포트 — PR #42 경계조건 모듈 리팩터링

**리뷰어:** Claude · **대상:** `feature/bc-refactor` → `main` · **변경:** 14 files, +612 −430

## 요약
경계조건 코드를 `BoundaryCondition` 클래스 계층으로 정리한 방향은 좋습니다. 다만 **주기 경계의 인덱스 오류(R1)** 는
결과를 조용히 틀리게 만드는 버그라 병합 전에 반드시 수정이 필요합니다.

| # | 심각도 | 위치 | 내용 |
|---|---|---|---|
| R1 | 🔴 높음 | `src/boundary.cpp:88` | 주기 경계 ghost cell off-by-one |
| R2 | 🟠 중간 | `src/boundary.cpp:141` | Neumann 경계에서 dx 대신 dy 사용 (정사각 격자에서만 우연히 맞음) |
| R3 | 🟡 낮음 | `include/bc.h:22` | 가상 소멸자 누락 |
| R4 | 🟡 낮음 | `tests/test_bc.cpp` | 비정사각 격자 테스트 없음 |

## R1. 주기 경계 ghost cell off-by-one
```cpp
// 변경 후 (오류)
p[idx(0, j)]      = p[idx(nx - 1, j)];
// 기대
p[idx(0, j)]      = p[idx(nx - 2, j)];
p[idx(nx - 1, j)] = p[idx(1, j)];
```
ghost cell 이 자기 자신(반대편 ghost)을 복사하므로 주기 방향 기울기가 0으로 수렴합니다.
Taylor–Green 와류 테스트에서 운동에너지 감쇠율이 해석해 대비 **11%** 느립니다.

## R2. Neumann 경계 격자 간격
`apply_neumann()` 의 y 방향 처리에서 `dx` 를 사용합니다. `nx != ny` 또는 `dx != dy` 인 경우 플럭스가 틀립니다.

## 권장 사항
- [ ] R1, R2 수정 후 비정사각 격자(64×128) 회귀 테스트 추가
- [ ] `BoundaryCondition` 에 `virtual ~BoundaryCondition() = default;`
- [x] 기존 단위 테스트 전부 통과 확인
"""
    clk.at(120)
    rv = s.add_artifact(tid4, path=f("review_PR42.md", review_md), title="PR #42 리뷰 리포트", category="review")
    s.add_artifact(tid4, path=f("review_PR42.pdf", simple_pdf("Code Review Report - PR #42", [
        "Reviewer: Claude    Target: feature/bc-refactor -> main    14 files, +612 -430", "",
        "## Summary",
        "Refactoring boundary conditions into a class hierarchy is a good direction.",
        "However R1 (periodic ghost-cell off-by-one) silently produces wrong results",
        "and must be fixed before merge.", "",
        "## Findings",
        "R1 [HIGH]   src/boundary.cpp:88   periodic ghost cell copies nx-1 instead of nx-2",
        "R2 [MEDIUM] src/boundary.cpp:141  Neumann BC uses dx for the y direction",
        "R3 [LOW]    include/bc.h:22       missing virtual destructor",
        "R4 [LOW]    tests/test_bc.cpp     no test for non-square grids", "",
        "## Evidence",
        "Taylor-Green vortex: kinetic-energy decay 11% slower than analytic solution.", "",
        "## Recommendations",
        "- Fix R1 and R2, add 64x128 regression test",
        "- Add virtual ~BoundaryCondition() = default;",
    ]), "wb"), title="PR #42 리뷰 리포트 (PDF)", category="review")
    clk.at(118); s.check_plan(tid4, [3])
    clk.at(40)
    s.request_approval(tid4, "리뷰 코멘트를 PR #42 에 게시해도 될까요?", kind="review", artifact_ids=[rv["id"]],
                       description="R1~R4 를 인라인 코멘트 4개 + 요약 코멘트 1개로 게시할 예정입니다. "
                                   "**Request changes** 상태로 제출합니다.")

    # ---------------------------------------------------------------- T-0005 논문 결과 섹션 (진행 중)
    clk.at(250)
    t5 = s.create_task(
        "논문 4장 결과 섹션 작성 (LaTeX)", type="paper", agent="Claude",
        instruction="`paper/chapters/results.tex` 에 4장 결과 섹션을 작성해 주세요.\n\n"
                    "1. 격자 수렴성 (Fig 4.1)\n2. GPU 성능 비교 (Fig 4.2, Table 4.1)\n3. 난류 통계 검증 (Fig 4.3)\n\n"
                    "수치는 반드시 `results/*.csv` 원본과 대조하고, 그림은 PDF 벡터로.",
        plan=["실험 결과 데이터 정리", "Fig 4.1–4.3 생성", "4.1절 격자 수렴성 작성", "4.2절 GPU 성능 작성",
              "Table 4.1 수치 검증", "4.3절 및 참고문헌 정리"], tags=["paper", "latex"])
    tid5 = t5["id"]
    clk.at(240); s.update_task(tid5, step="results/*.csv 12개 파일 정리", progress=5)
    clk.at(225); s.check_plan(tid5, [1])
    conv = [(h, 0.8 * h ** 2) for h in [1 / 16, 1 / 32, 1 / 64, 1 / 128, 1 / 256]]
    conv1 = [(h, 0.5 * h) for h in [1 / 16, 1 / 32, 1 / 64, 1 / 128, 1 / 256]]
    clk.at(200)
    fig1 = s.add_artifact(tid5, path=f("fig4_1_convergence.svg", svg_line_chart(
        [("L2 error", conv), ("1st order ref.", conv1)], "Fig 4.1  Grid convergence", "grid spacing h", "L2 error", logy=True)),
        title="Fig 4.1 격자 수렴성", description="기울기 ≈ 2.0 (2차 정확도 확인)")
    clk.at(185)
    s.add_artifact(tid5, path=f("fig4_2_speedup.svg", svg_bar_chart(
        ["CPU 64c", "A100", "MI250X 1GCD", "MI250X 8GCD"], [1.0, 18.4, 15.9, 112.3],
        "Fig 4.2  Speedup vs. 64-core CPU", "speedup", highlight=3)), title="Fig 4.2 GPU 가속비")
    s.check_plan(tid5, [2])
    clk.at(150)
    s.add_user_comment(tid5, "Fig 4.1 은 기준선으로 2차 정확도(h²) 선도 같이 넣어주세요.")
    with s.edit(tid5) as t:  # 에이전트가 이미 확인한 메시지
        t["log"][-1]["ack"] = True
    clk.at(140)
    s.add_log(tid5, "사용자 요청 반영: Fig 4.1 에 h² 기준선 추가 예정 (다음 그림 갱신 때 반영)", kind="info")
    clk.at(120); s.check_plan(tid5, [3])
    tex = r"""\section{Results}\label{sec:results}

\subsection{Grid convergence}
Figure~\ref{fig:convergence} shows the $L_2$ error of the pressure field against the
analytic Taylor--Green solution. The observed order of accuracy is $p = 2.01$,
confirming the second-order spatial discretisation.

\begin{figure}[t]
  \centering
  \includegraphics[width=0.8\linewidth]{figs/fig4_1_convergence.pdf}
  \caption{Grid convergence of the pressure solver.}
  \label{fig:convergence}
\end{figure}

\subsection{GPU performance}
On a single Frontier node (8 MI250X GCDs) the HIP port achieves a
$112\times$ speedup over a 64-core CPU baseline (Table~\ref{tab:perf}).

\begin{table}[t]
  \centering
  \begin{tabular}{lrr}
    \toprule
    Platform & Time/step [ms] & Speedup \\
    \midrule
    CPU (64 cores)  & 412.0 & 1.0 \\
    A100            &  22.4 & 18.4 \\
    MI250X (1 GCD)  &  25.9 & 15.9 \\
    MI250X (8 GCD)  &   3.67 & 112.3 \\
    \bottomrule
  \end{tabular}
  \caption{Wall-clock time per step on a $2048^2$ grid.}
  \label{tab:perf}
\end{table}
"""
    clk.at(60)
    s.add_artifact(tid5, path=f("results.tex", tex), title="results.tex (4.1–4.2절 초안)")
    clk.at(45)
    s.add_issue(tid5, "Table 4.1 의 MI250X 8GCD 시간이 원본 로그(3.74 ms)와 2% 차이", severity="medium",
                detail="`results/perf_8gcd.csv` 의 마지막 3회 평균은 3.74 ms. 표에는 최솟값 3.67 ms 가 들어감. 평균/최솟값 중 어느 기준으로 할지 확인 필요.")
    clk.at(15)
    s.update_task(tid5, step="4.2절 GPU 성능 서술 작성 중 — 강/약 스케일링 문단", progress=55)

    # ---------------------------------------------------------------- T-0006 HIP 포팅 (승인 대기, 실제 diff)
    clk.at(190)
    t6 = s.create_task(
        "압력 솔버 HIP 포팅 (MI250X)", type="code", agent="Claude", repo=repo,
        instruction="`src/pressure_solver.cu` 를 HIP 으로 포팅해 Frontier(MI250X)에서 돌아가게 해주세요.\n\n"
                    "- CPU 결과 대비 상대오차 1e-10 이내\n- 오류 체크 매크로 추가\n- **커밋 전 승인 필수**",
        plan=["CUDA 커널 구조 분석", "hipify 변환 및 빌드 설정", "MI250X 단일 GCD 성능 측정", "수치 검증 (CPU 대비)", "커밋 승인 요청"],
        tags=["hip", "frontier", "gpu"])
    tid6 = t6["id"]
    clk.at(185); s.update_task(tid6, step="CUDA 커널 1개, 메모리 전송 5곳 확인", progress=5)
    clk.at(175); s.check_plan(tid6, [1])
    clk.at(160); s.update_task(tid6, step="hipify-perl 변환 후 블록 크기 64×4 로 조정 (wavefront 64)")
    clk.at(150); s.check_plan(tid6, [2])
    clk.at(130)
    i6 = s.add_issue(tid6, "hipMemcpyAsync 이후 스트림 동기화 누락 → 간헐적 NaN", severity="high",
                     detail="결과 복사 전에 `hipStreamSynchronize` 가 없어 호스트가 덜 복사된 버퍼를 읽음 (10회 중 2회 재현).")
    clk.at(110); s.resolve_issue(tid6, i6["id"], note="`hipStreamSynchronize(stream)` 추가, 50회 반복 실행에서 재현 안 됨")
    clk.at(95)
    perf = s.add_artifact(tid6, path=f("perf_report.md", (
        "# MI250X 단일 GCD 성능\n\n| 격자 | CUDA (A100) | HIP (MI250X 1GCD) | 비율 |\n|---|---:|---:|---:|\n"
        "| 1024² | 5.8 ms | 6.6 ms | 0.88 |\n| 2048² | 22.4 ms | 25.9 ms | 0.86 |\n| 4096² | 91.0 ms | 101.7 ms | 0.89 |\n\n"
        "- 블록 64×4 가 16×16 대비 **14%** 빠름\n- 메모리 대역폭 사용률 1.31 TB/s (이론치의 82%)\n")),
        title="성능 측정 리포트")
    s.check_plan(tid6, [3])
    clk.at(60)
    s.add_log(tid6, "수치 검증 통과: CPU 대비 최대 상대오차 3.2e-13 (2048², 5000 iter)", kind="success")
    s.check_plan(tid6, [4])
    s.add_issue(tid6, "AMD clang 경고: 미사용 변수 `nz` (main.cpp)", severity="low")
    if repo:
        _git(repo, "mv", "src/pressure_solver.cu", "src/pressure_solver.hip")
        _write(os.path.join(repo, "src", "pressure_solver.hip"), SOLVER_V2)
        _write(os.path.join(repo, "src", "hip_utils.h"), HIP_UTILS)
        _write(os.path.join(repo, "CMakeLists.txt"), CMAKE_V2)
        clk.at(8)
        s.request_approval(
            tid6, "HIP 포팅 변경사항 커밋 · 푸시", artifact_ids=[perf["id"]],
            description="수치 검증(상대오차 3.2e-13)과 성능 측정을 마쳤습니다. 승인해 주시면 "
                        "`feature/hip-pressure` 브랜치에 커밋하겠습니다.\n\n"
                        "> 데모 저장소에는 원격이 없어 푸시는 생략됩니다. 승인 후 `devtrack --data <dir> apply T-0006 A-1` 로 실제 커밋을 확인할 수 있습니다.",
            commit={"repo": repo, "message": "Port pressure solver to HIP for MI250X\n\n"
                                             "- hipify kernels, 64x4 blocks for 64-wide wavefronts\n"
                                             "- add HIP_CHECK error macro\n- fix missing stream sync before D2H copy",
                    "push": False})

    # ---------------------------------------------------------------- T-0007 사용자가 웹에서 지시 (대기)
    clk.at(5)
    s.create_task(
        "난류 와도장 3D 가시화 (ParaView, Q-criterion)", type="visualization", agent="Claude", status="queued",
        created_by=store_mod.USER, priority="normal",
        instruction="`run_042/` 마지막 스냅샷으로 Q-criterion 등위면(Q=0.5) 을 속도 크기로 색칠한 그림을 만들어 주세요.\n\n"
                    "- 4K 해상도 PNG + 회전 애니메이션 mp4\n- 컬러맵: viridis")
