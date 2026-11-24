"""예시 데이터 생성 — 화면 확인용.

실제 git 저장소(<data>/.repos/cfd-solver)를 만들어 커밋 승인 요청의 diff 가 진짜이고,
웹에서 승인한 뒤 ``devtrack --data <dir> apply T-0001 A-1`` 로 실제 커밋까지 해볼 수 있다.
"""

import datetime as _dt
import json
import math
import os
import random
import re
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


def svg_line_chart(series, title, xlabel, ylabel, logy=False, w=640, h=380, legend="right"):
    pad_l, pad_r, pad_t, pad_b = 64, 20, 44, 52
    lx = pad_l + 12 if legend == "left" else w - pad_r - 150  # 범례 위치 (선이 지나가지 않는 위쪽 구석)
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
        out.append('<rect x="{}" y="{}" width="12" height="3" fill="{}"/>'.format(lx, pad_t + 4 + i * 18, c))
        out.append('<text x="{}" y="{}" font-size="12" fill="#374151">{}</text>'.format(lx + 18, pad_t + 9 + i * 18, name))
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


def _pdf_esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_pages(pages):
    """페이지별 content stream(ASCII) 목록 → PDF. 글꼴: F1 Helvetica, F2 Helvetica-Bold,
    F3 Times-Roman, F4 Times-Bold, F5 Times-Italic."""
    fonts = ["Helvetica", "Helvetica-Bold", "Times-Roman", "Times-Bold", "Times-Italic"]
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", None]
    objs += ["<< /Type /Font /Subtype /Type1 /BaseFont /{} /Encoding /WinAnsiEncoding >>".format(f).encode() for f in fonts]
    font_res = " ".join("/F{} {} 0 R".format(i + 1, i + 3) for i in range(len(fonts)))
    kids = []
    for content in pages:
        data = content.encode("latin-1")
        pid = len(objs) + 1
        kids.append("{} 0 R".format(pid))
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << {} >> >> /Contents {} 0 R >>"
                    .format(font_res, pid + 1).encode())
        objs.append(b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream")
    objs[1] = "<< /Type /Pages /Kids [{}] /Count {} >>".format(" ".join(kids), len(pages)).encode()
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


def simple_pdf(title, lines):
    """ASCII 텍스트 한 페이지짜리 PDF."""
    parts = ["BT /F2 18 Tf 56 780 Td ({}) Tj ET".format(_pdf_esc(title)),
             "0.8 0.8 0.8 RG 56 768 m 539 768 l S"]
    y = 744
    for line in lines:
        bold = line.startswith("## ")
        text = line[3:] if bold else line
        size = 13 if bold else 10.5
        if bold:
            y -= 6
        parts.append("BT /{} {} Tf 56 {} Td ({}) Tj ET".format("F2" if bold else "F1", size, y, _pdf_esc(text)))
        y -= 20 if bold else 15
    return pdf_pages(["\n".join(parts)])


class _Page:
    """논문 모양 데모 PDF 를 그리기 위한 아주 작은 도우미."""

    def __init__(self, num):
        self.ops, self.y, self.num = [], 770, num

    def text(self, s, x=72, size=10.5, font="F3", y=None):
        self.ops.append("BT /{} {} Tf {:.1f} {:.1f} Td ({}) Tj ET".format(font, size, x, self.y if y is None else y, _pdf_esc(s)))

    def para(self, s, size=10.5, width=98, lead=13.5, x=72, font="F3"):
        import textwrap
        for ln in textwrap.wrap(s, width):
            self.text(ln, x=x, size=size, font=font)
            self.y -= lead
        self.y -= 5

    def heading(self, s, size=12):
        self.y -= 8
        self.text(s, size=size, font="F4")
        self.y -= size + 7

    def line(self, x1, y1, x2, y2, w=0.6, rgb="0 0 0", dash=False):
        self.ops.append("q {} RG {} w {}{:.1f} {:.1f} m {:.1f} {:.1f} l S Q".format(
            rgb, w, "[3 2] 0 d " if dash else "", x1, y1, x2, y2))

    def poly(self, pts, rgb, w=1.2, dash=False):
        path = " ".join("{:.1f} {:.1f} {}".format(x, y, "m" if i == 0 else "l") for i, (x, y) in enumerate(pts))
        self.ops.append("q {} RG {} w {}{} S Q".format(rgb, w, "[3 2] 0 d " if dash else "", path))

    def rect(self, x, y, w, h, rgb="0.23 0.36 0.8"):
        self.ops.append("q {} rg {:.1f} {:.1f} {:.1f} {:.1f} re f Q".format(rgb, x, y, w, h))

    def box(self, x, y, w, h):
        self.ops.append("q 0.4 0.4 0.4 RG 0.6 w {:.1f} {:.1f} {:.1f} {:.1f} re S Q".format(x, y, w, h))

    def render(self):
        self.text(str(self.num), x=294, y=40, size=9.5)
        return "\n".join(self.ops)


def paper_pdf():
    """'컴파일된' 논문 4장 데모 PDF (3쪽)."""
    blue, orange = "0.23 0.36 0.8", "0.91 0.35 0.05"
    p1 = _Page(1)
    p1.text("GPU-Accelerated Pressure Solver for Exascale CFD", x=110, size=16, font="F4")
    p1.y -= 18
    p1.text("Draft -- Chapter 4: Results", x=228, size=10.5, font="F5")
    p1.y -= 14
    p1.line(72, p1.y, 523, p1.y, w=0.4, rgb="0.6 0.6 0.6")
    p1.y -= 6
    p1.heading("4   Results", size=14)
    p1.para("This chapter evaluates the HIP port of the pressure solver in terms of accuracy (Section 4.1), "
            "performance (Section 4.2) and physical fidelity (Section 4.3).")
    p1.heading("4.1   Grid convergence")
    p1.para("We measure the discretisation error against the analytic Taylor-Green solution using the relative L2 norm")
    p1.text("e_h = || p_h - p_exact ||_2 / || p_exact ||_2 ,        p = log2 ( e_2h / e_h )", x=150, font="F5", size=11)
    p1.text("(4.1)", x=500, size=10.5)
    p1.y -= 22
    p1.para("Figure 4.1 shows that the observed order of accuracy defined in (4.1) is p = 2.01, confirming the "
            "second-order spatial discretisation.(1)")
    top = p1.y - 10
    x0, y0, w, h = 170, top - 190, 260, 180
    p1.box(x0, y0, w, h)
    hs = [1 / 16, 1 / 32, 1 / 64, 1 / 128, 1 / 256]
    lx = lambda v: x0 + 10 + (math.log10(v) - math.log10(1 / 256)) / (math.log10(1 / 16) - math.log10(1 / 256)) * (w - 20)
    ly = lambda v: y0 + 10 + (math.log10(v) + 6) / 5.5 * (h - 20)
    p1.poly([(lx(v), ly(0.8 * v * v)) for v in hs], blue)
    p1.poly([(lx(v), ly(0.5 * v)) for v in hs], orange, dash=True)
    p1.text("grid spacing h", x=x0 + 95, y=y0 - 14, size=8.5, font="F1")
    p1.y = y0 - 32
    p1.para("Figure 4.1: Grid convergence of the pressure solver. The dashed line indicates first-order "
            "convergence for reference.", size=9.5, width=104)
    p1.line(72, 78, 200, 78, w=0.4)
    p1.text("(1) All runs use double precision and a fixed pseudo-time step of 1e-4.", y=66, size=8.5)

    p2 = _Page(2)
    p2.heading("4.2   GPU performance")
    p2.para("On a single GPU node (8 MI250X GCDs) the HIP port achieves a 112x speedup over a 64-core CPU "
            "baseline (Table 4.1 and Figure 4.2), comparable to results reported for other exascale CFD codes "
            "[1, ?]. Three factors dominate:")
    for b in ["64-wide wavefronts favour 64x4 thread blocks over the original 16x16;",
              "the kernel is memory-bound, reaching 1.31 TB/s (82% of peak);",
              "host-device transfers are overlapped using HIP streams."]:
        p2.text("-", x=86)
        p2.text(b, x=98)
        p2.y -= 14
    p2.y -= 12
    p2.text("Table 4.1: Wall-clock time per step on a 2048^2 grid.", x=170, size=9.5)
    p2.y -= 10
    cols = [160, 330, 420]
    p2.line(150, p2.y, 450, p2.y, w=1.0)
    p2.y -= 14
    for i, row in enumerate([("Platform", "Time/step [ms]", "Speedup"), ("CPU (64 cores)", "412.0", "1.0"),
                             ("A100", "22.4", "18.4"), ("MI250X (1 GCD)", "25.9", "15.9"), ("MI250X (8 GCD)", "3.67", "112.3")]):
        for x, cell in zip(cols, row):
            p2.text(cell, x=x, size=10, font="F4" if i == 0 or (i == 4 and x == 420) else "F3")
        if i == 0:
            p2.line(150, p2.y - 5, 450, p2.y - 5, w=0.5)
        p2.y -= 16
    p2.line(150, p2.y + 9, 450, p2.y + 9, w=1.0)
    p2.y -= 16
    base = p2.y - 170
    p2.box(170, base, 260, 165)
    for i, v in enumerate([1.0, 18.4, 15.9, 112.3]):
        hgt = v / 120 * 145
        p2.rect(190 + i * 60, base + 8, 36, hgt, orange if i == 3 else blue)
    p2.y = base - 20
    p2.para("Figure 4.2: Speedup relative to the 64-core CPU baseline.", size=9.5)

    p3 = _Page(3)
    p3.heading("4.3   Turbulence statistics")
    p3.text("[TODO: Finalise after T-0008 results are approved.]", font="F5", size=10, x=72)
    p3.y -= 18
    p3.para("The mean velocity profile agrees with the wind-tunnel measurements to within 2.1% (Figure 4.3a), and the "
            "velocity-fluctuation PDF shows a positive skewness S = 0.31 (Figure 4.3b).")
    top = p3.y - 10
    for k, x0 in enumerate([90, 320]):
        p3.box(x0, top - 140, 190, 130)
        if k == 0:
            pts = [(x0 + 8 + 174 * (i / 40), top - 132 + 112 * (1 - abs(2 * i / 40 - 1) ** 6)) for i in range(41)]
        else:
            pts = [(x0 + 8 + 174 * (i / 40), top - 132 + 112 * math.exp(-((i / 40 * 8 - 4) ** 2) / 2)) for i in range(41)]
        p3.poly(pts, blue)
        p3.text("({}) {}".format("a" if k == 0 else "b", "Mean velocity" if k == 0 else "Fluctuation PDF"),
                x=x0 + 45, y=top - 156, size=9.5)
    p3.y = top - 182
    p3.para("Figure 4.3: Comparison of DNS and experiment.", size=9.5)
    p3.heading("References")
    p3.para("[1] J. Smith, A. Lee, Exascale CFD on AMD GPUs, J. Comput. Phys. 489 (2023) 112261.", size=9.5)
    return pdf_pages([p1.render(), p2.render(), p3.render()])


RESULTS_TEX = r"""\section{Results}\label{sec:results}

This chapter evaluates the HIP port of the pressure solver in terms of accuracy
(Section~\ref{sec:convergence}), performance (Section~\ref{sec:perf}) and physical
fidelity (Section~\ref{sec:turb}).

\subsection{Grid convergence}\label{sec:convergence}
We measure the discretisation error against the analytic Taylor--Green solution
using the relative $L_2$ norm
\begin{equation}
  \varepsilon_h = \frac{\lVert p_h - p_{\mathrm{exact}} \rVert_2}{\lVert p_{\mathrm{exact}} \rVert_2},
  \qquad
  p = \log_2 \frac{\varepsilon_{2h}}{\varepsilon_h}.
  \label{eq:l2}
\end{equation}
Figure~\ref{fig:convergence} shows that the observed order of accuracy defined in
\eqref{eq:l2} is $p = 2.01$, confirming the second-order spatial
discretisation.\footnote{All runs use double precision and a fixed pseudo-time step
$\Delta\tau = 10^{-4}$.}

\begin{figure}[t]
  \centering
  \includegraphics[width=0.8\linewidth]{figs/fig4_1_convergence.pdf}
  \caption{Grid convergence of the pressure solver. The dashed line indicates
  first-order convergence for reference.}
  \label{fig:convergence}
\end{figure}

\subsection{GPU performance}\label{sec:perf}
On a single GPU node (8 MI250X GCDs) the HIP port achieves a $112\times$
speedup over a 64-core CPU baseline (Table~\ref{tab:perf} and
Figure~\ref{fig:speedup}), comparable to results reported for other exascale CFD
codes~\cite{smith2023,kim2022}. Three factors dominate:
\begin{itemize}
  \item 64-wide wavefronts favour $64\times4$ thread blocks over the original $16\times16$;
  \item the kernel is memory-bound, reaching 1.31\,TB/s (82\% of peak);
  \item host--device transfers are overlapped using HIP streams.
\end{itemize}

% TODO: strong/weak scaling paragraph
\begin{table}[t]
  \centering
  \caption{Wall-clock time per step on a $2048^2$ grid.}
  \label{tab:perf}
  \begin{tabular}{lrr}
    \toprule
    Platform & Time/step [ms] & Speedup \\
    \midrule
    CPU (64 cores)  & 412.0 & 1.0 \\
    A100            &  22.4 & 18.4 \\
    MI250X (1 GCD)  &  25.9 & 15.9 \\
    MI250X (8 GCD)  &   3.67 & \textbf{112.3} \\
    \bottomrule
  \end{tabular}
\end{table}

\begin{figure}[t]
  \centering
  \includegraphics[width=0.7\linewidth]{figs/fig4_2_speedup}
  \caption{Speedup relative to the 64-core CPU baseline.}
  \label{fig:speedup}
\end{figure}

\subsection{Turbulence statistics}\label{sec:turb}
\todo{Finalise after T-0008 results are approved.}
The mean velocity profile agrees with the wind-tunnel measurements to within 2.1\%
(Figure~\ref{fig:turb}a), and the velocity-fluctuation PDF shows a positive
skewness $S \approx 0.31$ (Figure~\ref{fig:turb}b).

\begin{figure}[t]
  \centering
  \begin{subfigure}{0.48\linewidth}
    \includegraphics[width=\linewidth]{figs/fig4_3a_profiles.pdf}
    \caption{Mean velocity}
  \end{subfigure}
  \hfill
  \begin{subfigure}{0.48\linewidth}
    \includegraphics[width=\linewidth]{figs/fig4_3b_pdf.pdf}
    \caption{Fluctuation PDF}
  \end{subfigure}
  \caption{Comparison of DNS and wind-tunnel experiment.}
  \label{fig:turb}
\end{figure}
"""


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

# 논문 심사 반복 예시 (T-0005 ↔ T-0013) — 원고 본문 · 참고문헌 · 2차 원고에서 고친 문장
PAPER_MAIN = r"""\documentclass{article}
\usepackage{amsmath,graphicx,booktabs}
\begin{document}
\title{A portable GPU pressure solver for incompressible flow}
\maketitle
\input{chapters/results}
\bibliography{refs}
\end{document}
"""
PAPER_BIB = """@article{roache1994,
  author = {Roache, P. J.},
  title = {Perspective: a method for uniform reporting of grid refinement studies},
  journal = {J. Fluids Eng.}, year = {1994}, volume = {116}, pages = {405--413}
}
"""
PAPER_RESPONSE = """# Response to the referee (round 2)

We thank the referee for the careful reading. Changes are marked in the revised manuscript.

**R1 (grid convergence).** We now report the observed order from three grids with Richardson
extrapolation and the grid convergence index (GCI) following Roache (1994), Section 4.1.

**R2 (Table 4.1).** All timings were re-checked against `results/perf.csv`; the 8-GCD speedup is
112.3x (previously rounded to 112x). Run-to-run variation (5 runs) is now given.

**R3 (Figure 4.1).** We will add the second-order reference line in the next revision.
"""


# Codex 리뷰(R1 · R2)를 반영한 판
SOLVER_V3 = SOLVER_V2.replace(
    "  // MI250X: 64-wide wavefronts -> 64x4 blocks\n  dim3 block(64, 4);\n  dim3 grid((f.nx + 63) / 64, (f.ny + 3) / 4);",
    "  // MI250X: 64-wide wavefronts -> 64x4 blocks\n  constexpr int BX = 64, BY = 4;\n  dim3 block(BX, BY);\n"
    "  dim3 grid((f.nx + BX - 1) / BX, (f.ny + BY - 1) / BY);").replace(
    "  HIP_CHECK(hipMemcpyAsync(f.p, d_p, bytes, hipMemcpyDeviceToHost, stream));",
    "  HIP_CHECK(hipGetLastError());  // kernel launch errors\n"
    "  HIP_CHECK(hipMemcpyAsync(f.p, d_p, bytes, hipMemcpyDeviceToHost, stream));")

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
# ---------------------------------------------------------------------------- 코드 리뷰 예시 (T-0004)
PR42_FINDINGS = {
    "pr": {"url": "https://github.com/example/cfd-solver/pull/42", "repo": "example/cfd-solver", "number": 42,
           "title": "경계조건 모듈 리팩터링", "author": "jlee", "base": "main", "head": "feature/bc-refactor",
           "head_sha": "3f9c2a1e7b5d4c09a8e6f1b2c3d4e5f60718293a", "files": 14, "additions": 612, "deletions": 430,
           "ci": "통과"},
    "verdict": "request_changes",
    "summary": "코드 정리 방향은 좋지만, **시뮬레이션 결과를 조용히 틀리게 만드는 문제(R1)** 를 고친 뒤 병합하는 것이 좋습니다. "
               "가로·세로 칸 크기가 다른 격자에서만 드러나는 계산 실수(R2)도 같이 고치기를 권합니다.",
    "findings": [
        {"id": "R1", "severity": "blocker", "title": "주기 경계 ghost cell off-by-one",
         "plain": "오른쪽 끝 값을 왼쪽 바깥 칸으로 복사해야 하는데 한 칸 어긋난 곳을 복사합니다. "
                  "계산은 멈추지 않고 결과만 조금씩 틀어져서 알아차리기 어렵습니다.",
         "file": "src/boundary.cpp", "line": 88, "category": "정확성", "confidence": "high"},
        {"id": "R2", "severity": "major", "title": "Neumann 경계에서 y 방향에 dx 사용",
         "plain": "세로 방향 경계를 계산하면서 가로 칸 크기를 씁니다. 정사각형 격자에서는 두 값이 같아 티가 나지 않습니다.",
         "file": "src/boundary.cpp", "line": 141, "category": "정확성", "confidence": "high"},
        {"id": "R3", "severity": "minor", "title": "BoundaryCondition 가상 소멸자 누락",
         "plain": "경계 객체를 지울 때 정리 코드가 실행되지 않을 수 있습니다. 지금은 괜찮지만 나중에 메모리 누수의 원인이 됩니다.",
         "file": "include/bc.h", "line": 22, "category": "안전성", "confidence": "high"},
        {"id": "R4", "severity": "minor", "title": "비정사각 격자 테스트 없음",
         "plain": "모든 테스트가 정사각형 격자만 써서 R2 같은 실수를 잡지 못합니다.",
         "file": "tests/test_bc.cpp", "category": "테스트", "confidence": "high"},
        {"id": "R5", "severity": "question", "title": "이전 설정 키 bc_type 을 계속 지원하나요?",
         "plain": "설정 파일에서 쓰던 이름이 바뀌었습니다. 기존 입력 파일이 그대로 동작하는지 확인이 필요합니다.",
         "file": "src/config.cpp", "line": 57, "category": "호환성", "confidence": "medium"},
    ],
}

PR42_REPORT = """# PR #42 코드 리뷰 – example/cfd-solver – {date}

> **한 줄 결론:** 코드 정리 방향은 좋지만, 시뮬레이션 결과를 조용히 틀리게 만드는 문제(R1)를 고친 뒤 병합하는 것이 좋습니다.

| PR | 작성자 | 대상 | 리뷰한 커밋 | 규모 | CI |
|---|---|---|---|---|---|
| [경계조건 모듈 리팩터링](https://github.com/example/cfd-solver/pull/42) | jlee | `main` ← `feature/bc-refactor` | `3f9c2a1e` | 14개 파일, +612 −430 | 통과 |

## 1. 한눈에 보기

- **이 PR이 하는 일:** 시뮬레이션 영역의 가장자리(경계)를 처리하는 코드를 정리합니다. 경계 종류마다 흩어져 있던 코드를 한 구조로 모아, 새 경계 조건을 추가하기 쉽게 만듭니다.
- **리뷰 의견:** 수정 후 다시 확인 — 정리 방향은 좋지만 계산 결과에 영향을 주는 문제가 2개 있습니다.
- **꼭 고쳐야 할 것:** 2개
  - **R1** — 주기 경계(한쪽 끝과 반대쪽 끝이 이어진 경계)에서 값을 잘못된 칸에서 복사합니다. 그 결과 흐름이 실제보다 느리게 약해집니다 (테스트에서 11% 차이).
  - **R2** — 가로·세로 칸 크기가 다른 격자에서 경계의 기울기를 잘못 계산합니다. 정사각형 격자에서는 우연히 맞아서 기존 테스트로는 드러나지 않았습니다.
- **영향 범위:** 주기 경계 조건(R1), 또는 비정사각 격자에서 Neumann 경계(R2)를 쓰는 모든 계산.

| 중요도 | 뜻 |
|---|---|
| 🔴 반드시 수정 (Blocker) | 병합 전에 꼭 고쳐야 함 — 결과가 틀리거나 프로그램이 멈춤 |
| 🟠 수정 권장 (Major) | 특정 조건에서 동작이나 결과에 영향을 줌 |
| 🟡 개선 제안 (Minor) | 지금도 동작하지만 고치면 더 안전하거나 읽기 쉬움 |
| ⚪ 사소함 (Nit) | 이름 · 표기 · 스타일 수준 |
| 🔵 질문 (Question) | 작성자에게 의도를 확인할 부분 |

## 2. 검토 결과 요약

| # | 중요도 | 쉬운 설명 | 위치 | 분류 | 확신도 |
|---|---|---|---|---|---|
| R1 | 🔴 반드시 수정 | 주기 경계 값을 한 칸 어긋난 곳에서 복사해 결과가 틀어짐 | `src/boundary.cpp:88` | 정확성 | 높음 |
| R2 | 🟠 수정 권장 | 가로·세로 간격이 다르면 경계 기울기가 틀림 | `src/boundary.cpp:141` | 정확성 | 높음 |
| R3 | 🟡 개선 제안 | 객체를 지울 때 정리 코드가 실행되지 않을 수 있음 | `include/bc.h:22` | 안전성 | 높음 |
| R4 | 🟡 개선 제안 | R2 같은 실수를 잡아낼 테스트가 없음 | `tests/test_bc.cpp` | 테스트 | 높음 |
| R5 | 🔵 질문 | 예전 설정 이름(`bc_type`)도 계속 쓸 수 있는지 | `src/config.cpp:57` | 호환성 | 중간 |

## 3. 변경 사항

| 영역 | 파일 | +/− | 변경 내용 |
|---|---|---|---|
| 경계조건 구조 | `include/bc.h`, `src/boundary.cpp` | +410 −380 | 경계 종류별 함수를 `BoundaryCondition` 클래스 계층으로 통합 |
| 설정 | `src/config.cpp` | +62 −31 | 설정 키 `bc_type` → `boundary.kind` |
| 테스트 | `tests/test_bc.cpp` | +140 −19 | 클래스별 단위 테스트 추가 |

경계 처리를 가상 함수 `apply()` 하나로 통일해 호출하는 쪽 코드가 단순해졌습니다.
시간 루프 안에서 가상 함수 호출이 늘었지만 경계 칸 수가 적어 성능 영향은 무시할 수준입니다.

## 4. 상세 (개발자용)

### R1. 주기 경계 ghost cell off-by-one — 🔴 반드시 수정

- **쉽게 말하면:** 오른쪽 끝 값을 왼쪽 바깥 칸으로 복사해야 하는데, 한 칸 어긋난 곳을 복사합니다. 계산은 멈추지 않고 결과만 조금씩 틀어져서 알아차리기 어렵습니다.
- **위치:** [`src/boundary.cpp:88`](https://github.com/example/cfd-solver/blob/3f9c2a1e7b5d4c09a8e6f1b2c3d4e5f60718293a/src/boundary.cpp#L88) (`apply_periodic()`)
- **문제:** ghost cell `0` 이 반대편 ghost cell `nx-1` 을 복사합니다. 내부 마지막 칸 `nx-2` 를 복사해야 합니다.
- **왜 중요한가:** 주기 방향 기울기가 0 쪽으로 끌려가, Taylor–Green 와류 테스트에서 운동에너지 감쇠율이 해석해보다 **11%** 느립니다.
- **제안:**

  ```cpp
  p[idx(0, j)]      = p[idx(nx - 2, j)];
  p[idx(nx - 1, j)] = p[idx(1, j)];
  ```

- **확신도:** 높음 — 코드에서 직접 확인, 테스트로 재현

### R2. Neumann 경계의 격자 간격 — 🟠 수정 권장

- **쉽게 말하면:** 세로 방향 경계를 계산하면서 가로 칸 크기를 씁니다. 정사각형 격자에서는 두 값이 같아 티가 나지 않습니다.
- **위치:** `src/boundary.cpp:141` (`apply_neumann()`)
- **문제:** y 방향 플럭스 계산에 `dx` 를 사용합니다.
- **왜 중요한가:** `dx != dy` 인 격자에서 벽면 플럭스가 `dx/dy` 배만큼 틀립니다.
- **제안:** y 방향 분기에서 `dx` → `dy`
- **확신도:** 높음 — 코드에서 직접 확인

### R3. 가상 소멸자 누락 — 🟡 개선 제안

- **쉽게 말하면:** 경계 객체를 지울 때 하위 클래스의 정리 코드가 실행되지 않을 수 있습니다. 지금은 문제가 없지만, 나중에 메모리를 쓰는 경계 조건을 추가하면 누수가 생깁니다.
- **위치:** `include/bc.h:22`
- **제안:** `virtual ~BoundaryCondition() = default;`
- **확신도:** 높음

### R4. 비정사각 격자 테스트 없음 — 🟡 개선 제안

- **쉽게 말하면:** 모든 테스트가 정사각형 격자만 써서 R2 같은 실수를 잡지 못합니다.
- **위치:** `tests/test_bc.cpp`
- **제안:** 64×128 격자에서 Neumann · 주기 경계 회귀 테스트 추가
- **확신도:** 높음

### R5. 이전 설정 키 호환성 — 🔵 질문

- **쉽게 말하면:** 설정 파일에서 쓰던 이름이 바뀌었습니다. 기존 입력 파일이 그대로 동작하는지 확인이 필요합니다.
- **위치:** `src/config.cpp:57`
- **질문:** `bc_type` 을 계속 읽을 계획인가요? 아니라면 오류 메시지로 새 이름을 안내하면 좋겠습니다.
- **확신도:** 중간 — 다른 곳에서 변환하는지 diff 만으로는 확인할 수 없음

## 5. 테스트 · 검증 제안

- R1, R2 수정 후 64×128 격자 회귀 테스트 추가
- Taylor–Green 와류 감쇠율을 해석해와 비교 (오차 1% 이내 기대)

## 6. 종합 의견

**Request changes** 를 제안합니다. 구조 개선은 병합할 가치가 있지만, R1 은 결과를 조용히 틀리게 만들어 병합 전에 수정이 필요합니다.
R2 도 같은 PR 에서 고치는 것이 안전합니다. 최종 결정은 사용자가 합니다.

## 용어 풀이

- **경계 조건:** 시뮬레이션 영역의 가장자리에서 값을 어떻게 정할지에 대한 규칙
- **주기 경계:** 한쪽 끝을 나가면 반대쪽 끝으로 들어오는 경계 (예: 지구를 한 바퀴 도는 방향)
- **ghost cell:** 경계 계산을 위해 영역 바깥에 덧붙인 가상의 칸
- **Neumann 경계:** 경계에서 값 자체가 아니라 기울기(변화율)를 정하는 조건
- **가상 소멸자:** C++ 에서 객체를 지울 때 하위 클래스의 정리 코드까지 실행되게 하는 선언
"""


# ------------------------------------------------------------------ 문서 리뷰 예시 (논문 원고 · 제안서)
def _xml_esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _docx_runs(text):
    """'**굵게**', '^위첨자^', '[^1]'(각주) 표시가 있는 글 → <w:r> 목록."""
    out = []
    for part in re.split(r"(\*\*.+?\*\*|\^.+?\^|\[\^\d+\])", text):
        if not part:
            continue
        if part.startswith("**"):
            out.append('<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">{}</w:t></w:r>'.format(_xml_esc(part[2:-2])))
        elif part.startswith("[^"):
            out.append('<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteReference w:id="{}"/></w:r>'
                       .format(part[2:-1]))
        elif part.startswith("^"):
            out.append('<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>{}</w:t></w:r>'.format(_xml_esc(part[1:-1])))
        else:
            out.append('<w:r><w:t xml:space="preserve">{}</w:t></w:r>'.format(_xml_esc(part)))
    return "".join(out)


def simple_docx(blocks, footnotes=None):
    """작은 Word 문서(.docx) 바이트. blocks: [(종류, 글)] — 종류는 title · h1 · h2 · p · bullet · num,
    ('table', [[칸, …], …]) 는 표. 제목 스타일 ID 는 한국어 Word 처럼 숫자("1", "2")로 둔다."""
    import io
    import zipfile
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    style = {"title": "Title", "h1": "1", "h2": "2"}
    body = []
    for kind, val in blocks:
        if kind == "table":
            rows = "".join("<w:tr>{}</w:tr>".format("".join(
                "<w:tc><w:p>{}</w:p></w:tc>".format(_docx_runs(c)) for c in row)) for row in val)
            body.append("<w:tbl><w:tblPr><w:tblStyle w:val=\"TableGrid\"/></w:tblPr>{}</w:tbl>".format(rows))
            continue
        ppr = ""
        if kind in style:
            ppr = '<w:pPr><w:pStyle w:val="{}"/></w:pPr>'.format(style[kind])
        elif kind in ("bullet", "num"):
            ppr = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="{}"/></w:numPr></w:pPr>'.format(
                1 if kind == "bullet" else 2)
        body.append("<w:p>{}{}</w:p>".format(ppr, _docx_runs(val)))
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
            '</Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>'),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"/>'
            '</Relationships>'),
        "word/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {}>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="a"><w:name w:val="Normal"/></w:style>'
            '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/></w:rPr></w:style>'
            '<w:style w:type="paragraph" w:styleId="2"><w:name w:val="heading 2"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
            '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
            '<w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/>'
            '<w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV w:val="single" w:sz="4"/>'
            '</w:tblBorders></w:tblPr></w:style></w:styles>').format(w),
        "word/numbering.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering {}>'
            '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/>'
            '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
            '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            '<w:lvlText w:val="%1."/><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
            '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
            '</w:numbering>').format(w),
        "word/footnotes.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:footnotes {}>'
            '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
            '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
            '{}</w:footnotes>').format(w, "".join(
                '<w:footnote w:id="{}"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> {}</w:t></w:r></w:p></w:footnote>'
                .format(k, _xml_esc(v)) for k, v in sorted((footnotes or {}).items()))),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {}><w:body>{}'
            '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr></w:body></w:document>').format(w, "".join(body)),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


MANUSCRIPT_TEX = r"""\documentclass[11pt]{article}
\usepackage{amsmath,graphicx}
\title{Near-wall velocity skewness in low-Reynolds-number turbulent channel flow:\\ DNS and hot-wire comparison}
\author{H. Kang \and J. Lee}
\begin{document}
\maketitle

\begin{abstract}
We study the skewness of the streamwise velocity near the wall of a turbulent channel flow using
direct numerical simulation (DNS) and hot-wire measurements. The results shows good agreement between
the two approaches. Our findings reveal a universal near-wall behaviour of the skewness.
\end{abstract}

\section{Introduction}\label{sec:intro}
Near-wall turbulence statistics have been studied extensively \cite{kim1987,moser1999,lee2015}.
Higher-order moments such as the skewness factor are sensitive to the resolution of both simulations and
probes. To our knowledge, this is the first study of near-wall skewness.

\section{Methods}\label{sec:methods}
The DNS was performed at $Re_\tau = 180$ on a $256 \times 128 \times 256$ grid. The wind-tunnel datas were
obtained with a single hot-wire probe of length $1\,\mathrm{mm}$ at matching friction Reynolds number.

\section{Results}\label{sec:results}
The results shows that the skewness $S_u$ reaches $S_u = 0.98$ at the wall in the DNS and $1.09$ in the
experiment, a difference of 10.7\%. It can be seen that the skewness is seen to increase towards the wall.
In the other hand, the skewness changes sign at $y^+ \approx 15$ in both data sets.

\section{Conclusions}\label{sec:conclusions}
The near-wall skewness is universal and independent of the Reynolds number. The DNS and the experiment
agree well, which validates both approaches.

\begin{thebibliography}{9}
\bibitem{kim1987} J. Kim, P. Moin, R. Moser, J. Fluid Mech. 177 (1987) 133--166.
\bibitem{moser1999} R. Moser, J. Kim, N. Mansour, Phys. Fluids 11 (1999) 943--945.
\bibitem{lee2015} M. Lee, R. Moser, J. Fluid Mech. 774 (2015) 395--415.
\end{thebibliography}
\end{document}
"""

MANUSCRIPT_REVIEW = {
    "doc": {
        "title": "Near-wall velocity skewness in low-Reynolds-number turbulent channel flow: DNS and hot-wire comparison",
        "kind": "paper", "purpose": "Journal of Fluid Mechanics 투고 전 점검", "audience": "난류 분야 심사위원 · 편집자",
        "authors": "H. Kang, J. Lee", "pages": 4, "words": 2850, "format": "LaTeX", "source": "F-1",
        "synopsis": "난류 채널 유동에서 **벽 가까이의 흐름 방향 속도 왜도**(분포가 한쪽으로 치우친 정도)를 "
                    "직접 수치 모사(DNS, Re_τ = 180)와 풍동 열선 측정으로 비교한 원고입니다.\n\n"
                    "- 벽에서의 왜도는 DNS 0.98, 실험 1.09 로 10.7% 차이가 나며, 두 자료 모두 y⁺ ≈ 15 에서 부호가 바뀝니다.\n"
                    "- 저자들은 이를 근거로 두 방법이 잘 맞고, 벽 근처 왜도가 레이놀즈 수와 무관한 *보편적* 성질이라고 결론짓습니다.",
    },
    "verdict": "major_revision",
    "summary": "DNS 와 실험을 같은 조건으로 맞춘 비교는 탄탄하지만, **선행 연구와 무엇이 다른지(R1)** 와 "
               "**결론의 일반화 근거(R2)** 가 부족해 지금 투고하면 대폭 수정 요청을 받을 가능성이 큽니다.",
    "checks": [
        {"area": "목적 부합", "rating": "fair", "note": "JFM 투고에 필요한 새로운 기여가 본문에 드러나지 않음"},
        {"area": "구성 · 형식", "rating": "fair", "note": "초록에 정량 결과 없음, 방법 절에 격자 해상도 근거 없음"},
        {"area": "선행 연구와의 차이", "rating": "poor", "note": "인용만 있고 차이 설명 없음, '첫 연구' 주장은 사실과 다름"},
        {"area": "논리 전개", "rating": "fair", "note": "10.7% 차이를 오차 범위 없이 '잘 맞는다'고 판단"},
        {"area": "결론의 근거", "rating": "poor", "note": "레이놀즈 수 하나로 '보편적' 결론"},
        {"area": "문장 · 표현", "rating": "fair", "note": "같은 뜻을 반복하는 문장, 용어 혼용"},
        {"area": "맞춤법 · 표기", "rating": "fair", "note": "문법 오류 4곳"},
    ],
    "strengths": [
        "DNS 와 풍동 실험을 같은 마찰 레이놀즈 수로 맞춰 비교한 설계가 명확합니다.",
        "왜도의 부호가 바뀌는 위치(y⁺ ≈ 15)를 두 자료에서 함께 확인한 점이 설득력 있습니다.",
    ],
    "findings": [
        {"id": "R1", "severity": "blocker", "title": "선행 연구와의 차이가 설명되지 않음 · '첫 연구' 주장이 사실과 다름",
         "plain": "참고문헌 세 편(Kim 1987, Moser 1999, Lee 2015)이 이미 벽 근처 왜도를 보고했는데, 이 원고가 무엇을 새로 "
                  "보였는지 설명이 없습니다. 심사위원이 가장 먼저 지적할 부분입니다.",
         "location": "1쪽 · 1. Introduction 둘째 문단", "category": "선행 연구", "confidence": "high",
         "quote": "To our knowledge, this is the first study of near-wall skewness.",
         "suggestion": "While near-wall skewness has been reported from DNS [kim1987, moser1999, lee2015], it has not been "
                       "compared directly with hot-wire data at matched Re_tau, accounting for probe length. This study ..."},
        {"id": "R2", "severity": "blocker", "title": "레이놀즈 수 하나(Re_τ=180)로 '보편적' 결론",
         "plain": "한 가지 조건에서만 계산 · 측정했는데 '레이놀즈 수와 무관하다'고 결론 내렸습니다. 근거보다 주장이 큽니다.",
         "location": "3쪽 · 5. Conclusions 첫 문장", "category": "결론 · 근거", "confidence": "high",
         "quote": "The near-wall skewness is universal and independent of the Reynolds number.",
         "suggestion": "At Re_tau = 180, the near-wall skewness from DNS and experiment agree within 11%; "
                       "testing its Reynolds-number dependence requires additional cases."},
        {"id": "R3", "severity": "major", "title": "10.7% 차이를 오차 범위 없이 '잘 맞는다'고 판단",
         "plain": "실험의 측정 오차(불확도)와 DNS 통계 오차를 밝히지 않아, 10.7% 가 작은 차이인지 판단할 수 없습니다.",
         "location": "2쪽 · 4. Results 첫 문단", "category": "논리", "confidence": "high"},
        {"id": "R4", "severity": "major", "title": "초록에 정량 결과가 없고, 방법 절에 격자 해상도 근거가 없음",
         "plain": "초록만 읽어서는 무엇을 얼마나 알아냈는지 알 수 없습니다. 격자가 벽 근처를 충분히 촘촘하게 나눴는지(Δy⁺)도 빠졌습니다.",
         "location": "1쪽 · Abstract, 2쪽 · 3. Methods", "category": "구성 · 형식", "confidence": "high"},
        {"id": "R5", "severity": "minor", "title": "'skewness factor' 와 'skewness' 혼용",
         "plain": "같은 양을 두 이름으로 부르면 다른 양으로 오해할 수 있습니다. 하나로 통일하세요.",
         "location": "1쪽 · 1. Introduction, 2쪽 · 4. Results", "category": "문장 · 표현", "confidence": "high"},
        {"id": "R6", "severity": "question", "title": "열선 길이(1 mm)의 공간 평균 효과를 검토했나요?",
         "plain": "열선이 1 mm 구간을 평균해서 재기 때문에 벽 근처 왜도가 달라질 수 있습니다. 10.7% 차이의 원인일 수 있습니다.",
         "location": "2쪽 · 3. Methods", "category": "논리", "confidence": "medium"},
        {"id": "R7", "severity": "nit", "title": "주어 · 동사 수 일치", "location": "1쪽 Abstract, 2쪽 4. Results",
         "quote": "The results shows", "suggestion": "The results show", "category": "맞춤법 · 표기"},
        {"id": "R8", "severity": "nit", "title": "'data' 는 이미 복수형", "location": "2쪽 · 3. Methods",
         "quote": "The wind-tunnel datas were obtained", "suggestion": "The wind-tunnel data were obtained",
         "category": "맞춤법 · 표기"},
        {"id": "R9", "severity": "nit", "title": "관용 표현 오류", "location": "2쪽 · 4. Results",
         "quote": "In the other hand,", "suggestion": "On the other hand,", "category": "맞춤법 · 표기"},
        {"id": "R10", "severity": "nit", "title": "같은 뜻을 반복하는 문장", "location": "2쪽 · 4. Results",
         "quote": "It can be seen that the skewness is seen to increase towards the wall.",
         "suggestion": "The skewness increases towards the wall.", "category": "문장 · 표현"},
    ],
}

MANUSCRIPT_REPORT = """# 문서 리뷰: 근벽 왜도 DNS · 실험 비교 원고 – {date}

> **한 줄 결론:** 비교 실험 자체는 탄탄하지만, 선행 연구와의 차이(R1)와 결론의 일반화 근거(R2)를 보완하지 않으면 JFM 에서 대폭 수정 요청을 받을 가능성이 큽니다.

| 문서 | 종류 | 목적 | 분량 | 형식 |
|---|---|---|---|---|
| Near-wall velocity skewness in low-Reynolds-number turbulent channel flow | 과학 논문 | Journal of Fluid Mechanics 투고 전 점검 | 4쪽, 약 2,850단어 | LaTeX |

## 1. 한눈에 보기

- **이 문서는:** 벽 가까이의 속도 왜도를 수치 모사(DNS)와 풍동 측정으로 비교하고, 그 성질이 보편적이라고 주장하는 논문 원고입니다.
- **리뷰 의견:** 대폭 보완 — 새로운 기여와 결론의 근거가 원고에 드러나지 않습니다.
- **꼭 고쳐야 할 것:** 2개
  - **R1** — 이미 같은 주제를 다룬 논문 세 편을 인용하면서 "첫 연구"라고 씁니다. 무엇이 새로운지 밝혀야 합니다.
  - **R2** — 한 가지 조건만 보고 "레이놀즈 수와 무관하다"고 결론 냅니다.

| 점검 영역 | 평가 | 메모 |
|---|---|---|
| 목적 부합 | 보완 필요 | 새로운 기여가 드러나지 않음 |
| 구성 · 형식 | 보완 필요 | 초록에 정량 결과 없음 |
| 선행 연구와의 차이 | 미흡 | 인용만 있고 차이 설명 없음 |
| 논리 전개 | 보완 필요 | 오차 범위 없이 일치 판단 |
| 결론의 근거 | 미흡 | 조건 하나로 일반화 |
| 문장 · 표현 | 보완 필요 | 반복 표현, 용어 혼용 |
| 맞춤법 · 표기 | 보완 필요 | 문법 오류 4곳 |

## 2. 문서 요약

DNS(Re_τ = 180)와 열선 풍속계 측정으로 벽 근처 흐름 방향 속도의 왜도를 비교했습니다. 벽에서의 왜도는 DNS 0.98,
실험 1.09 로 10.7% 차이가 나고, 두 자료 모두 y⁺ ≈ 15 에서 부호가 바뀝니다. 저자들은 두 방법이 잘 맞으며
벽 근처 왜도가 보편적이라고 결론짓습니다.

## 3. 목적 대비 보완할 부분

JFM 은 새로운 물리적 이해를 요구합니다. 지금 원고는 기존 DNS 결과를 실험으로 다시 확인한 수준으로 읽힙니다.
**열선 길이를 고려한 직접 비교**가 새로운 점이라면 서론과 초록에서 그것을 분명히 내세워야 합니다 (R1, R6).

## 4. 과학 논문 점검: 형식 · 선행 연구 · 결론의 근거

- **형식:** 초록에 수치 결과가 없고(R4), 방법 절에 벽 근처 격자 간격(Δy⁺)과 통계 표본 수가 없습니다.
- **선행 연구와의 차이:** R1 참고.
- **결론의 근거:** 결론 두 문장 모두 근거보다 큽니다. 조건을 한정하거나(R2) 실험 불확도를 함께 제시해야 합니다(R3).

## 5. 수정 · 보완 항목

| # | 중요도 | 쉬운 설명 | 위치 | 분류 |
|---|---|---|---|---|
| R1 | 🔴 반드시 수정 | 무엇이 새로운지 설명이 없고 "첫 연구" 주장이 틀림 | 1쪽 Introduction | 선행 연구 |
| R2 | 🔴 반드시 수정 | 조건 하나로 "보편적" 결론 | 3쪽 Conclusions | 결론 · 근거 |
| R3 | 🟠 수정 권장 | 오차 범위 없이 "잘 맞는다" | 2쪽 Results | 논리 |
| R4 | 🟠 수정 권장 | 초록에 수치 없음, 격자 근거 없음 | 1–2쪽 | 구성 · 형식 |
| R5 | 🟡 개선 제안 | 같은 양을 두 이름으로 부름 | 1–2쪽 | 문장 · 표현 |
| R6 | 🔵 질문 | 열선 길이 효과를 검토했는지 | 2쪽 Methods | 논리 |

## 6. 문장 · 맞춤법

| # | 위치 | 원문 | 수정안 |
|---|---|---|---|
| R7 | 1쪽 Abstract, 2쪽 Results | The results shows | The results show |
| R8 | 2쪽 Methods | The wind-tunnel datas were obtained | The wind-tunnel data were obtained |
| R9 | 2쪽 Results | In the other hand, | On the other hand, |
| R10 | 2쪽 Results | It can be seen that the skewness is seen to increase towards the wall. | The skewness increases towards the wall. |

## 7. 종합 의견

대폭 보완 후 투고를 권합니다. R1 · R2 를 고치고 실험 불확도(R3)를 더하면 원고의 주장과 근거가 맞아집니다.
최종 판단은 저자가 합니다.

## 용어 풀이

- **왜도(skewness):** 값의 분포가 평균을 기준으로 한쪽으로 치우친 정도
- **DNS:** 난류의 모든 크기의 소용돌이를 직접 계산하는 수치 모사
- **y⁺:** 벽에서의 거리를 벽 근처 흐름의 길이 척도로 나눈 값
"""

PROPOSAL_DOCX = [
    ("title", "차세대 난류 시뮬레이션을 위한 계산 자원 제안서 (초안)"),
    ("h1", "1. 연구 목표"),
    ("num", "벽 근처 난류 구조를 Re_τ = 1000 까지 직접 수치 모사로 규명한다."),
    ("num", "DNS 자료로 **데이터 기반 벽 모델**을 개발한다."),
    ("h1", "2. 연구 방법"),
    ("p", "GPU 로 포팅한 압력 솔버(HIP)를 사용해 세 가지 레이놀즈 수에서 조건마다 10^5^ 시간 단계를 계산한다.[^1] 계산 결과는 몇일 간격으로 검증할수 있다."),
    ("bullet", "Re_τ = 180, 550, 1000 세 조건"),
    ("bullet", "풍동 실험 자료와 비교 검증"),
    ("h1", "3. 계산 자원 요청"),
    ("table", [["조건", "격자 수", "노드시간"], ["Re_τ = 180", "0.8억", "5,000"], ["Re_τ = 550", "6억", "15,000"],
               ["Re_τ = 1000", "30억", "30,000"], ["합계", "", "50,000"]]),
    ("h1", "4. 기대 효과"),
    ("p", "본 연구는 난류 모델링 분야에서 중요한 역활을 할 것이며 산업계에도 큰 도움이 될 것이다."),
]
PROPOSAL_FOOTNOTES = {1: "솔버 성능은 T-0001 · T-0006 작업에서 검증했다."}

PROPOSAL_REVIEW = {
    "doc": {"title": "차세대 난류 시뮬레이션을 위한 계산 자원 제안서 (초안)", "kind": "proposal",
            "purpose": "HPC 계산 시간 신청 (심사위원 평가 대비)", "audience": "계산 자원 배분 심사위원",
            "pages": 2, "format": "Word (.docx)", "source": "F-1",
            "synopsis": "GPU 로 포팅한 압력 솔버로 세 가지 레이놀즈 수의 채널 유동 DNS 를 수행하고, 그 자료로 "
                        "데이터 기반 벽 모델을 만들겠다는 계산 자원 제안서입니다. 총 **50,000 노드시간**을 요청합니다."},
    "verdict": "minor_revision",
    "summary": "구성은 깔끔하지만 심사에서 가장 중요한 **노드시간 산정 근거(R1)** 가 없습니다. 목표 2 에 대한 방법(R2)도 채워 주세요.",
    "checks": [
        {"area": "목적 부합", "rating": "fair", "note": "심사 기준인 자원 산정 근거가 빠짐"},
        {"area": "구성 · 형식", "rating": "good"},
        {"area": "논리 전개", "rating": "fair", "note": "목표 2(벽 모델)에 대응하는 방법이 없음"},
        {"area": "문장 · 표현", "rating": "fair", "note": "기대 효과가 추상적"},
        {"area": "맞춤법 · 표기", "rating": "fair", "note": "맞춤법 · 띄어쓰기 3곳"},
    ],
    "strengths": ["목표 · 방법 · 자원 · 기대 효과 순서로 심사 양식에 맞게 구성되어 있습니다.",
                  "GPU 솔버 검증 이력을 각주로 밝혀 신뢰를 줍니다."],
    "findings": [
        {"id": "R1", "severity": "major", "title": "노드시간 산정 근거가 없음 (표의 50,000 노드시간)",
         "plain": "심사위원은 요청한 계산 시간이 타당한지를 가장 먼저 봅니다. 격자 수 × 시간 단계 × 측정한 1단계 비용으로 계산한 근거를 넣어야 합니다.",
         "location": "3. 계산 자원 요청 · 표", "category": "목적 · 내용", "confidence": "high"},
        {"id": "R2", "severity": "major", "title": "목표 2(데이터 기반 벽 모델)에 대한 방법이 없음",
         "plain": "목표는 두 개인데 방법 절에는 DNS 계산만 있습니다. 벽 모델을 어떻게 학습 · 검증할지 한두 문단이 필요합니다.",
         "location": "2. 연구 방법", "category": "논리", "confidence": "high"},
        {"id": "R3", "severity": "minor", "title": "기대 효과가 추상적",
         "plain": "'큰 도움이 될 것' 대신 어떤 계산이 얼마나 빨라지거나 정확해지는지 수치로 쓰면 설득력이 커집니다.",
         "location": "4. 기대 효과", "category": "문장 · 표현", "confidence": "medium",
         "quote": "산업계에도 큰 도움이 될 것이다.",
         "suggestion": "벽 모델 LES 의 계산 비용을 DNS 대비 1/100 로 줄여, 항공기 날개 설계 해석에 바로 쓸 수 있다."},
        {"id": "R4", "severity": "nit", "title": "맞춤법: 몇일 → 며칠", "location": "2. 연구 방법",
         "quote": "몇일 간격으로", "suggestion": "며칠 간격으로", "category": "맞춤법 · 표기"},
        {"id": "R5", "severity": "nit", "title": "띄어쓰기: 할수 → 할 수", "location": "2. 연구 방법",
         "quote": "검증할수 있다", "suggestion": "검증할 수 있다", "category": "맞춤법 · 표기"},
        {"id": "R6", "severity": "nit", "title": "맞춤법: 역활 → 역할", "location": "4. 기대 효과",
         "quote": "중요한 역활을", "suggestion": "중요한 역할을", "category": "맞춤법 · 표기"},
    ],
}

PROPOSAL_REPORT = """# 문서 리뷰: 계산 자원 제안서 (초안) – {date}

> **한 줄 결론:** 구성은 심사 양식에 맞지만, 요청한 50,000 노드시간의 산정 근거(R1)와 벽 모델 방법(R2)을 채워야 심사를 통과할 수 있습니다.

| 문서 | 종류 | 목적 | 분량 | 형식 |
|---|---|---|---|---|
| 차세대 난류 시뮬레이션을 위한 계산 자원 제안서 (초안) | 제안서 | HPC 계산 시간 신청 | 2쪽 | Word |

## 1. 한눈에 보기

- **이 문서는:** GPU 솔버로 세 조건의 난류 DNS 를 하고, 그 자료로 벽 모델을 만들겠다며 계산 시간을 요청하는 제안서입니다.
- **리뷰 의견:** 소폭 수정 — 근거 두 가지만 보완하면 됩니다.
- **꼭 고쳐야 할 것:** 없음 (수정 권장 2개: R1, R2)

## 2. 문서 요약

Re_τ = 180, 550, 1000 세 조건에서 DNS 를 수행하고, 풍동 자료와 비교 검증한 뒤 데이터 기반 벽 모델을 개발합니다.
계산 자원으로 총 50,000 노드시간을 요청합니다.

## 3. 목적 대비 보완할 부분

계산 자원 심사는 **요청량의 타당성**과 **솔버의 준비 상태**를 봅니다. 준비 상태는 각주로 밝혔지만, 요청량 산정 근거가 없습니다 (R1).

## 4. 수정 · 보완 항목

| # | 중요도 | 쉬운 설명 | 위치 |
|---|---|---|---|
| R1 | 🟠 수정 권장 | 노드시간을 어떻게 계산했는지 없음 | 3. 계산 자원 요청 |
| R2 | 🟠 수정 권장 | 목표 2 에 대한 방법이 없음 | 2. 연구 방법 |
| R3 | 🟡 개선 제안 | 기대 효과를 수치로 | 4. 기대 효과 |

## 5. 문장 · 맞춤법

| # | 원문 | 수정안 |
|---|---|---|
| R4 | 몇일 간격으로 | 며칠 간격으로 |
| R5 | 검증할수 있다 | 검증할 수 있다 |
| R6 | 중요한 역활을 | 중요한 역할을 |

## 6. 종합 의견

R1 · R2 를 보완하면 제출해도 좋습니다.
"""


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
        "CI 파이프라인에 압력 솔버 단위 테스트 추가", type="code", agent="Codex", repo=repo,
        instruction="압력 솔버(CPU 참조 구현)에 대해 **manufactured solution** 기반 단위 테스트를 추가하고 "
                    "GitHub Actions 에서 자동 실행되도록 해주세요.\n\n- 격자 32, 64 두 경우\n- 허용 오차 5e-3",
        plan=["기존 테스트 구조 확인", "manufactured solution 테스트 작성", "CI 워크플로 추가", "커밋 승인 요청"],
        tags=["ci", "test"], model="gpt-6.1-sol", effort="high")
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
        s.apply_approval(tid1, ap["id"], author="Codex")
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
        "Transformer 서로게이트 모델 학습 (GPU 노드 1개, 8 GCD)", type="model", agent="Claude", priority="high",
        instruction="CFD 스냅샷 1,200개로 압력장 예측 Transformer 를 학습해 주세요.\n\n"
                    "- 노드 1개 (MI250X 8 GCD), PyTorch DDP\n- 목표: 검증 상대 L2 오차 < 3%\n- 체크포인트는 `$SCRATCH/proj/ckpt` 에 저장",
        plan=["데이터 로더 구성", "모델/학습 스크립트 작성", "소규모 디버그 학습", "본 학습 (200 epoch)", "평가 및 리포트"],
        tags=["pytorch", "hpc"])
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

    # ---------------------------------------------------------------- T-0004 PR 코드 리뷰 (항목 선택 대기)
    clk.at(300)
    t4 = s.create_task(
        "PR #42 코드 리뷰: 경계조건 모듈 리팩터링", type="review", agent="Claude",
        repo_url="https://github.com/example/cfd-solver/pull/42",
        instruction="/review-pr https://github.com/example/cfd-solver/pull/42\n\n"
                    "정확성 버그 위주로 봐 주세요. 리포트는 개발자가 아닌 사람도 이해할 수 있게 써 주세요.",
        plan=["PR 정보 · 변경사항 수집", "코드 검토", "리뷰 리포트 작성", "코멘트할 항목 선택 (사용자)",
              "코멘트 초안 작성 · 게시 승인", "PR 에 게시"], tags=["PR-42"])
    tid4 = t4["id"]
    clk.at(290); s.update_task(tid4, step="PR 정보 수집: 14개 파일, +612 −430, CI 통과", progress=10)
    clk.at(280); s.check_plan(tid4, [1])
    clk.at(220); s.update_task(tid4, step="경계조건 코드 검토 중 (boundary.cpp, bc.h)", progress=35)
    clk.at(170); s.check_plan(tid4, [2])
    clk.at(160); s.update_task(tid4, step="리뷰 리포트 작성 중", progress=60)
    clk.at(122)
    s.set_review(tid4, PR42_FINDINGS)
    clk.at(120)
    rv = s.add_artifact(tid4, path=f("review-PR-42.md", PR42_REPORT.format(date=_dt.date.today().isoformat())),
                        title="PR #42 리뷰 리포트", category="review")
    s.add_artifact(tid4, path=f("review-PR-42.pdf", simple_pdf("Code Review Report - PR #42", [
        "Reviewer: Claude    Target: main <- feature/bc-refactor    14 files, +612 -430", "",
        "## Bottom line",
        "Good refactoring direction, but R1 silently produces wrong results",
        "and must be fixed before merge.", "",
        "## Findings",
        "R1 [BLOCKER]  src/boundary.cpp:88   periodic ghost cell copies nx-1 instead of nx-2",
        "R2 [MAJOR]    src/boundary.cpp:141  Neumann BC uses dx for the y direction",
        "R3 [MINOR]    include/bc.h:22       missing virtual destructor",
        "R4 [MINOR]    tests/test_bc.cpp     no test for non-square grids",
        "R5 [QUESTION] src/config.cpp:57     is the old key bc_type still accepted?", "",
        "## Evidence",
        "Taylor-Green vortex: kinetic-energy decay 11% slower than analytic solution.", "",
        "## Verdict",
        "Request changes (fix R1 and R2, add a 64x128 regression test).",
    ]), "wb"), title="PR #42 리뷰 리포트 (PDF)", category="review")
    clk.at(118); s.check_plan(tid4, [3])
    clk.at(40)
    s.update_task(tid4, step="사용자가 코멘트할 항목을 고르기를 기다리는 중", progress=70)
    s.request_approval(tid4, "PR #42: 코멘트를 작성할 항목을 골라주세요", kind="review", artifact_ids=[rv["id"]],
                       choices=[], choices_label="PR 코멘트를 작성할 항목",
                       description="고른 항목만 GitHub 코멘트 초안을 만들어 **다시 확인받은 뒤** 게시합니다. "
                                   "아직 PR 에는 아무것도 올리지 않았습니다.\n\n"
                                   "제안: **R1, R2** (결과에 영향) — 리뷰 의견은 *수정 후 다시 확인* (Request changes)")

    # ---------------------------------------------------------------- T-0005 논문 결과 섹션 (진행 중)
    clk.at(250)
    t5 = s.create_task(
        "논문 4장 결과 섹션 작성 (LaTeX)", type="paper", agent="Claude",
        instruction="`paper/chapters/results.tex` 에 4장 결과 섹션을 작성해 주세요.\n\n"
                    "1. 격자 수렴성 (Fig 4.1)\n2. GPU 성능 비교 (Fig 4.2, Table 4.1)\n3. 난류 통계 검증 (Fig 4.3)\n\n"
                    "수치는 반드시 `results/*.csv` 원본과 대조하고, 그림은 PDF 벡터로.",
        plan=["실험 결과 데이터 정리", "Fig 4.1–4.3 생성", "4.1절 격자 수렴성 작성", "4.2절 GPU 성능 작성",
              "Table 4.1 수치 검증", "4.3절 및 참고문헌 정리"], tags=["paper", "latex"], related=[tid2],
        review={"reviewer": "Codex", "model": "gpt-6-astra", "effort": "high", "rounds": 3,
                "doc": os.path.join(s.root, ".repos", "jfm-paper", "main.tex"), "venue": "Journal of Fluid Mechanics"})
    tid5 = t5["id"]
    clk.at(240); s.update_task(tid5, step="results/*.csv 12개 파일 정리", progress=5)
    clk.at(225); s.check_plan(tid5, [1])
    conv = [(h, 0.8 * h ** 2) for h in [1 / 16, 1 / 32, 1 / 64, 1 / 128, 1 / 256]]
    conv1 = [(h, 0.5 * h) for h in [1 / 16, 1 / 32, 1 / 64, 1 / 128, 1 / 256]]
    clk.at(200)
    fig1 = s.add_artifact(tid5, path=f("fig4_1_convergence.svg", svg_line_chart(
        [("L2 error", conv), ("1st order ref.", conv1)], "Fig 4.1  Grid convergence", "grid spacing h", "L2 error", logy=True,
        legend="left")),
        title="Fig 4.1 격자 수렴성", description="기울기 ≈ 2.0 (2차 정확도 확인)")
    clk.at(185)
    s.add_artifact(tid5, path=f("fig4_2_speedup.svg", svg_bar_chart(
        ["CPU 64c", "A100", "MI250X 1GCD", "MI250X 8GCD"], [1.0, 18.4, 15.9, 112.3],
        "Fig 4.2  Speedup vs. 64-core CPU", "speedup", highlight=3)), title="Fig 4.2 GPU 가속비")
    s.check_plan(tid5, [2])
    clk.at(150)  # 결과물 화면의 '결과물 수정 요청 · 추가 지시' 칸에서 보낸 메시지 → 에이전트가 같은 그림의 새 버전으로 반영
    s.add_user_comment(tid5, "Fig 4.1 은 기준선으로 2차 정확도(h²) 선도 같이 넣어주세요.", artifact=fig1["id"])
    s.ack_inbox(s.inbox(task=tid5))
    clk.at(140)
    conv2 = [(h, 2.0 * h ** 2) for h in [1 / 16, 1 / 32, 1 / 64, 1 / 128, 1 / 256]]
    s.add_artifact(tid5, path=f("fig4_1_convergence.svg", svg_line_chart(
        [("L2 error", conv), ("1st order ref.", conv1), ("2nd order ref. (h²)", conv2)],
        "Fig 4.1  Grid convergence", "grid spacing h", "L2 error", logy=True, legend="left")),
        title="Fig 4.1 격자 수렴성", replace_same_name=True)
    s.add_log(tid5, "사용자 요청 반영: Fig 4.1 에 h² 기준선 추가 ({} v2)".format(fig1["id"]), kind="info")
    clk.at(120); s.check_plan(tid5, [3])
    clk.at(60)
    s.add_artifact(tid5, path=f("results.tex", RESULTS_TEX), title="results.tex (4장 초안)")
    clk.at(58)
    s.record_build(tid5, "paper/main.tex", {
        "status": "failed", "engine": "latexmk -pdf", "duration": 4.8, "pages": None, "warnings": {},
        "errors": [{"file": "results.tex", "line": 58, "message": "Undefined control sequence."}],
        "log_text": ("This is pdfTeX, Version 3.141592653-2.6-1.40.25 (TeX Live 2023)\n(./main.tex\n"
                     "(./chapters/results.tex\n./chapters/results.tex:58: Undefined control sequence.\n"
                     "l.58     \\toprule\n\nNo pages of output.\n"),
    })
    clk.at(56)
    s.add_log(tid5, "원인: booktabs 패키지 누락 → preamble 에 \\usepackage{booktabs} 추가 후 재컴파일", kind="info")
    clk.at(54)
    s.record_build(tid5, "paper/main.tex", {
        "status": "ok", "engine": "latexmk -pdf", "duration": 6.2, "pages": 3, "errors": [],
        "pdf": f("main.pdf", paper_pdf(), "wb"),
        "warnings": {"undefined_citations": ["kim2022"], "undefined_references": [], "overfull": 1,
                     "underfull": 0, "other": []},
    })
    clk.at(45)
    s.add_issue(tid5, "Table 4.1 의 MI250X 8GCD 시간이 원본 로그(3.74 ms)와 2% 차이", severity="medium",
                detail="`results/perf_8gcd.csv` 의 마지막 3회 평균은 3.74 ms. 표에는 최솟값 3.67 ms 가 들어감. 평균/최솟값 중 어느 기준으로 할지 확인 필요.")
    clk.at(15)
    s.update_task(tid5, step="4.2절 GPU 성능 서술 작성 중 — 강/약 스케일링 문단", progress=55)

    # ---------------------------------------------------------------- T-0006 HIP 포팅 (승인 대기, 실제 diff)
    clk.at(190)
    t6 = s.create_task(
        "압력 솔버 HIP 포팅 (MI250X)", type="code", agent="Claude", repo=repo,
        instruction="`src/pressure_solver.cu` 를 HIP 으로 포팅해 AMD GPU(MI250X)에서 돌아가게 해주세요.\n\n"
                    "- CPU 결과 대비 상대오차 1e-10 이내\n- 오류 체크 매크로 추가\n- **커밋 전 승인 필수**",
        plan=["CUDA 커널 구조 분석", "hipify 변환 및 빌드 설정", "MI250X 단일 GCD 성능 측정", "수치 검증 (CPU 대비)", "커밋 승인 요청"],
        tags=["hip", "hpc", "gpu"], model="claude-opus-5-5", effort="xhigh",
        review={"reviewer": "Codex", "model": "gpt-6-astra", "effort": "high", "rounds": 3})
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
    # 저장소 변경 · Codex 리뷰 반복 · 커밋 승인 요청은 맨 끝에서 (리뷰 작업이 T-0012 가 되도록 — 다른 예시의 번호 유지)

    # ---------------------------------------------------------------- T-0007 사용자가 웹에서 지시 (대기)
    clk.at(5)
    s.create_task(
        "난류 와도장 3D 가시화 (ParaView, Q-criterion)", type="visualization", agent="누구나", status="queued",
        created_by=store_mod.USER, priority="normal",
        instruction="`run_042/` 마지막 스냅샷으로 Q-criterion 등위면(Q=0.5) 을 속도 크기로 색칠한 그림을 만들어 주세요.\n\n"
                    "- 4K 해상도 PNG + 회전 애니메이션 mp4\n- 컬러맵: viridis")

    # ---------------------------------------------------------------- T-0008 논문용 난류 통계 분석 (결과 확인 요청)
    clk.at(230)
    t8 = s.create_task(
        "논문 4.3절용 난류 통계 검증 분석 (DNS vs 실험)", type="analysis", agent="Claude", related=[tid5],
        instruction="논문 4.3절(난류 통계 검증)에 쓸 분석입니다.\n\n"
                    "- DNS `run_042` 와 풍동 실험(T-0002)의 평균 속도 · 속도 변동 통계 비교\n"
                    "- 속도 변동 확률밀도(PDF) 비교\n- 논문용 그림은 벡터(SVG/PDF), 수치는 표로 정리",
        plan=["DNS 통계량 추출 (2,000 스냅샷 시간 평균)", "실험 데이터 좌표계 정렬", "프로파일 · PDF 비교 그림",
              "오차 정량화 및 요약"], tags=["paper", "turbulence"])
    tid8 = t8["id"]
    clk.at(220); s.update_task(tid8, step="DNS 스냅샷 2,000개 시간 평균 계산 (sbatch job 2319011)", progress=10)
    clk.at(170); s.check_plan(tid8, [1])
    clk.at(160)
    i8 = s.add_issue(tid8, "실험 데이터 x 좌표 원점이 DNS 와 0.05 m 어긋남", severity="medium",
                     detail="실험은 노즐 출구, DNS 는 유입 경계 기준. 이동 후 비교해야 함.")
    clk.at(150); s.resolve_issue(tid8, i8["id"], note="실험 좌표를 +0.05 m 이동해 정렬")
    s.check_plan(tid8, [2])
    ys = [i / 40 for i in range(41)]
    dns = [(y, 1 - abs(2 * y - 1) ** 6) for y in ys]
    exp_pts = [(y, max(0.0, 1 - abs(2 * y - 1) ** 6 + 0.015 * math.sin(9 * y))) for y in ys[::4]]
    clk.at(110)
    fa = s.add_artifact(tid8, path=f("fig4_3a_profiles.svg", svg_line_chart(
        [("DNS", dns), ("experiment", exp_pts)], "Fig 4.3(a)  Mean velocity profile", "y / H", "U / U_c")),
        title="Fig 4.3(a) 평균 속도 프로파일 비교", description="실험 좌표 정렬 후. 최대 차이 2.1% (y/H = 0.2)")
    xs = [-4 + i * 0.2 for i in range(41)]
    gauss = [(x, math.exp(-x * x / 2) / math.sqrt(2 * math.pi)) for x in xs]
    skew = [(x, max(1e-4, math.exp(-x * x / 2) / math.sqrt(2 * math.pi) * (1 + 0.3 * (x ** 3 - 3 * x) / 6))) for x in xs]
    clk.at(100)
    fb = s.add_artifact(tid8, path=f("fig4_3b_pdf.svg", svg_line_chart(
        [("Gaussian", gauss), ("DNS u'", skew)], "Fig 4.3(b)  PDF of streamwise velocity fluctuation", "u' / u_rms", "PDF")),
        title="Fig 4.3(b) 속도 변동 PDF", description="왜도 S = 0.31 (양의 꼬리 두꺼움)")
    s.check_plan(tid8, [3])
    clk.at(70)
    s.add_artifact(tid8, path=f("turb_stats_comparison.csv", (
        "quantity,dns,experiment,rel_error_pct\n"
        "U_c [m/s],12.40,12.31,0.73\nu_rms_peak [m/s],1.87,1.83,2.14\ny_peak / H,0.115,0.120,4.35\n"
        "skewness,0.31,0.28,10.7\nflatness,3.42,3.35,2.09\n")), title="난류 통계 비교표 (Table 4.2 후보)")
    clk.at(50)
    summ = s.add_artifact(tid8, path=f("validation_summary.md", (
        "# 4.3절 난류 통계 검증 요약\n\n"
        "| 항목 | DNS | 실험 | 상대오차 |\n|---|---:|---:|---:|\n"
        "| 중심선 속도 U_c | 12.40 | 12.31 | 0.7% |\n| u_rms 최대값 | 1.87 | 1.83 | 2.1% |\n"
        "| 왜도 | 0.31 | 0.28 | 10.7% |\n\n"
        "## 발견\n1. 평균 속도 프로파일은 전 구간에서 **2.1% 이내**로 일치\n"
        "2. 속도 변동 PDF 는 양의 왜도(S≈0.3)를 보이며 실험과 경향이 같음\n"
        "3. 왜도 차이(10.7%)는 실험 열선 풍속계의 공간 분해능 한계로 추정 — 본문에 한계로 명시 권장\n")),
        title="검증 결과 요약")
    s.check_plan(tid8, [4])
    clk.at(35)
    s.set_note(tid8, (
        "# {} 인계 메모 — 4.3절 난류 통계 검증\n"
        "기준: DNS `run_042` (스냅샷 2,000개 시간 평균) · 풍동 실험 T-0002 정리본\n\n"
        "## 결론 (지금까지)\n"
        "- 평균 속도 프로파일은 전 구간에서 2.1% 이내로 일치 (확인됨)\n"
        "- 속도 변동 PDF 는 양의 왜도 — DNS S = 0.31, 실험 0.28 (차이 10.7%, 열선 공간 분해능 한계로 추정)\n\n"
        "## 핵심 숫자\n"
        "- U_c 12.40 vs 12.31 m/s (0.7%) · u_rms 최대 1.87 vs 1.83 m/s (2.1%) — 표: `turb_stats_comparison.csv`\n\n"
        "## 파일 · 데이터 위치\n"
        "- 그림: Fig 4.3(a)(b) 결과물 (SVG) · 요약: `validation_summary.md`\n\n"
        "## 주의할 점\n"
        "- 실험 x 좌표는 노즐 출구 기준 → DNS 와 비교 전 +0.05 m 이동해야 함 (I-1)\n\n"
        "## 남은 일\n"
        "- 4.3절 수치 확정 승인 대기 (A-1)\n").format(tid8))
    clk.at(30)
    s.request_approval(tid8, "논문 4.3절에 사용할 그림 · 수치 확정", kind="result",
                       artifact_ids=[summ["id"], fa["id"], fb["id"]],
                       description="Fig 4.3(a)(b)와 비교표의 수치로 4.3절을 작성해도 될까요? "
                                   "왜도 차이(10.7%)는 측정 한계로 본문에 명시할 예정입니다.")

    # ---------------------------------------------------------------- T-0009 후속 연구 브레인스토밍 (아이디어 고르기)
    clk.at(600)
    t9 = s.create_task(
        "DNS run_042 후속 연구 아이디어 브레인스토밍", type="brainstorm", agent="Claude", related=[tid8],
        instruction="DNS `run_042` 와 풍동 실험 데이터로 다음에 할 수 있는 연구를 같이 생각해 주세요.\n\n"
                    "- 6개월 안에 논문 한 편이 나올 규모\n- 지금 있는 데이터와 계산 시간(HPC 50,000 노드시간) 안에서\n"
                    "- 아이디어마다 필요한 것 · 위험 요소 · 기대 효과를 함께",
        plan=["가진 데이터 · 자원 정리", "아이디어 목록 (1차)", "고른 아이디어 구체화", "첫 작업 정하기"],
        tags=["idea", "turbulence"])
    tid9 = t9["id"]
    clk.at(595); s.update_task(tid9, step="가진 데이터 · 계산 자원 정리 (DNS 2,000 스냅샷, 풍동 40 런, T-0003 서로게이트)", progress=10)
    clk.at(580); s.check_plan(tid9, [1])
    clk.at(560)
    ideas = s.add_artifact(tid9, path=f("ideas_round1.md", (
        "# 후속 연구 아이디어 (1차)\n\n"
        "| # | 아이디어 | 필요한 것 | 위험 | 규모 |\n|---|---|---|---|---|\n"
        "| I1 | 데이터 기반 벽 모델 — DNS 벽 근처 통계로 LES 벽 모델 보정 | 벽 근처 스냅샷, 학습용 GPU | 다른 레이놀즈 수로 일반화 | 중 |\n"
        "| I2 | 레이놀즈 수 의존성 — Re_τ 두 단계 추가 계산 | 약 30,000 노드시간 | 계산 시간 대부분 소모 | 대 |\n"
        "| I3 | 왜도 차이 원인 규명 — 열선 풍속계 공간 필터를 DNS 에 적용 | 기존 데이터만 | 차이가 다른 원인일 수 있음 | 소 |\n"
        "| I4 | 서로게이트(T-0003)에 물리 제약 추가 | 연속 방정식 손실 항 | 학습 불안정 | 중 |\n"
        "| I5 | 와류 구조 자동 추적 — Q-criterion 등위면 시간 추적 | 시간 간격 촘촘한 스냅샷 | 저장 공간 | 중 |\n\n"
        "## 제안\n- **I3** 은 T-0008 결과(왜도 차이 10.7%)에 바로 이어지고 계산이 거의 들지 않습니다.\n"
        "- **I1** 은 논문 기여가 가장 크지만 일반화 검증이 필요합니다.\n")),
        title="아이디어 목록 (1차)")
    clk.at(555)
    ap9 = s.request_approval(
        tid9, "더 구체화할 아이디어를 골라주세요", kind="plan", artifact_ids=[ideas["id"]],
        choices=["I1=데이터 기반 벽 모델 (LES 벽 모델 보정)", "I2=레이놀즈 수 의존성 (추가 계산 2회)",
                 "I3=왜도 차이 원인 규명 (공간 필터 비교)", "I4=서로게이트에 물리 제약 추가",
                 "I5=와류 구조 자동 추적"],
        choices_label="더 구체화할 아이디어",
        description="아이디어 5개를 정리했습니다. 고른 아이디어만 방법 · 일정 · 필요한 계산 시간까지 구체화합니다.")
    clk.at(500)
    s.decide(tid9, ap9["id"], "approve", selected=["I1", "I3"],
             comment="I1 과 I3 이 좋겠습니다. I3 은 T-0008 결과와 바로 이어지니 우선순위를 높게 봐 주세요.")
    s.ack_decision(tid9, ap9["id"])
    clk.at(495); s.check_plan(tid9, [2])
    clk.at(490); s.add_user_comment(tid9, "I1 은 HPC 계산 시간이 얼마나 들지도 같이 봐 주세요.")
    s.ack_inbox(s.inbox(task=tid9))
    clk.at(470); s.add_log(tid9, "I1 예비 학습 계산량 추정: 약 8,000 노드시간 (스냅샷 300개, 3회 반복)")
    clk.at(420)
    detail = s.add_artifact(tid9, path=f("ideas_round2.md", (
        "# 고른 아이디어 구체화 — I1, I3\n\n"
        "## I3. 왜도 차이 원인 규명 (먼저 하기 좋음)\n"
        "- **목표:** DNS 와 실험의 왜도 차이(10.7%)가 열선 풍속계의 공간 분해능 때문인지 확인\n"
        "- **방법:** 열선 길이(1 mm)만큼 DNS 속도장을 공간 평균한 뒤 왜도를 다시 계산\n"
        "- **필요한 것:** 기존 DNS 스냅샷만 — 계산 시간 거의 없음\n"
        "- **일정:** 2주 · 결과는 논문 4.3절 한계 설명에 바로 사용\n\n"
        "## I1. 데이터 기반 벽 모델\n"
        "- **목표:** DNS 벽 근처 통계로 LES 벽 모델 계수를 보정\n"
        "- **방법:** 벽 전단응력 ↔ 바깥 속도 관계를 작은 신경망으로 학습, 다른 받음각 풍동 데이터로 검증\n"
        "- **필요한 것:** 약 8,000 노드시간, 학습용 GPU\n"
        "- **위험:** 레이놀즈 수가 다른 유동으로의 일반화 — 검증 데이터가 부족할 수 있음\n"
        "- **일정:** 6주\n")),
        title="고른 아이디어 구체화 (I1 · I3)")
    clk.at(410); s.check_plan(tid9, [3])
    clk.at(400); s.update_task(tid9, step="I1 · I3 중 먼저 시작할 아이디어를 정하는 중")
    s.request_approval(
        tid9, "어느 아이디어로 첫 작업을 시작할까요?", kind="plan", artifact_ids=[detail["id"]],
        choices=["I3=왜도 차이 원인 규명 — 2주, 계산 거의 없음", "I1=데이터 기반 벽 모델 — 6주, 약 8,000 노드시간"],
        choices_label="먼저 시작할 아이디어",
        description="고른 아이디어로 새 작업(데이터 분석 · 코드 개발)을 만들어 진행합니다. 둘 다 고르면 I3 부터 시작합니다.")

    # ---------------------------------------------------------------- T-0010 논문 원고 문서 리뷰 (리포트 확인 대기)
    clk.at(260)
    t10 = s.create_task(
        "문서 리뷰: JFM 투고 원고 (근벽 왜도 DNS · 실험 비교)", type="docreview", agent="Claude", related=[tid5],
        instruction="/review-doc ~/papers/skewness/main.tex\n\n- 문서 종류: 과학 논문\n"
                    "- 문서의 목적 · 제출처: Journal of Fluid Mechanics 투고 전 점검\n\n"
                    "선행 연구와의 차별점과 결론을 이끄는 논리를 특히 꼼꼼히 봐 주세요.",
        plan=["원고 읽기 · 요약", "목적 대비 미비한 부분", "논리 · 선행 연구 · 결론 점검", "문장 · 맞춤법 점검",
              "리뷰 리포트(PDF) 작성", "리포트 확인 (사용자)"], tags=["JFM"])
    tid10 = t10["id"]
    clk.at(255)
    s.add_artifact(tid10, path=f("main.tex", MANUSCRIPT_TEX), title="검토할 원고: main.tex", category="source")
    s.update_task(tid10, step="원고 읽기 · 요약 (4쪽, 약 2,850단어)", progress=10)
    clk.at(240); s.check_plan(tid10, [1])
    clk.at(230); s.update_task(tid10, step="목적(JFM 투고) 대비 빠진 부분 점검", progress=25)
    clk.at(215); s.check_plan(tid10, [2])
    clk.at(205); s.update_task(tid10, step="선행 연구 3편과 비교, 결론의 근거 점검", progress=45)
    clk.at(196)
    s.add_issue(tid10, "참고문헌 3편이 이미 벽 근처 왜도를 보고 — 원고의 '첫 연구' 주장과 맞지 않음", severity="high",
                detail="Kim et al. (1987), Moser et al. (1999), Lee & Moser (2015). 리포트 R1 로 정리합니다.")
    clk.at(185); s.check_plan(tid10, [3])
    clk.at(180); s.update_task(tid10, step="문장 · 맞춤법 점검", progress=65)
    clk.at(170); s.check_plan(tid10, [4])
    clk.at(150)
    s.set_review(tid10, MANUSCRIPT_REVIEW)
    today = _dt.date.today().isoformat()
    s.add_artifact(tid10, path=f("doc-review-skewness.md", MANUSCRIPT_REPORT.format(date=today)),
                   title="문서 리뷰 리포트 (Markdown)", category="review")
    rep10 = s.add_artifact(tid10, path=f("doc-review-skewness.pdf", simple_pdf("Document Review - JFM manuscript", [
        "Near-wall velocity skewness in low-Re turbulent channel flow (4 pages, LaTeX)", "",
        "## Bottom line",
        "Solid DNS / hot-wire comparison, but the novelty over prior work (R1) and the",
        "basis for a 'universal' conclusion (R2) are missing. Verdict: major revision.", "",
        "## Must fix",
        "R1  Difference from Kim 1987, Moser 1999, Lee 2015 not explained ('first study' claim)",
        "R2  'Universal' conclusion drawn from a single Re_tau = 180 case", "",
        "## Should fix",
        "R3  10.7% difference called 'good agreement' without uncertainty",
        "R4  No numbers in the abstract; grid resolution not justified", "",
        "## Wording and grammar",
        "R7  The results shows -> The results show",
        "R8  datas -> data      R9  In the other hand -> On the other hand",
    ]), "wb"), title="문서 리뷰 리포트 (PDF)", category="review")
    clk.at(148); s.resolve_issue(tid10, "I-1", note="리포트 R1 (반드시 수정) 으로 정리")
    clk.at(146); s.check_plan(tid10, [5])
    clk.at(145)
    s.update_task(tid10, step="리뷰 리포트 확인을 기다리는 중", progress=90)
    s.request_approval(
        tid10, "원고 리뷰 리포트를 확인해 주세요", kind="result", artifact_ids=[rep10["id"]],
        description="요약 · 점검 영역 · 보완할 부분(R1–R10)을 PDF 로 정리했습니다.\n\n"
                    "- **반드시 수정:** R1 선행 연구와의 차이, R2 결론의 일반화\n"
                    "- 원하시면 고를 항목만 원고(main.tex)에 반영한 수정안을 따로 만들어 드립니다.")
    # 시각화 요청 (/visualize-doc): 사용자가 메시지 칸의 '시각화 요청' 으로 보냄 → 요약 + 도식 2개 (분류 visual)
    clk.at(125)
    s.add_user_comment(tid10, "이 문서를 시각화해 주세요 (/visualize-doc) — 짜임새 있는 요약과 핵심 모식도 · 문서 구조 도식.\n"
                              "초점 (선택): 두 자료(DNS · 실험)가 어떻게 비교되는지")
    clk.at(118); s.ack_inbox(s.inbox(task=tid10))
    s.update_task(tid10, step="시각화: 원고 구조 · 핵심 주장 정리, 도식 설계 (Parallel Dual-Stream · Linear Pipeline)")
    assets = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_assets", "visualize")
    clk.at(104)
    for name, title in (("v1-overview.svg", "도식 1: DNS vs hot-wire 왜도 비교"), ("v2-structure.svg", "도식 2: 원고의 구조"),
                        ("visual-skewness.md", "시각화 요약")):
        with open(os.path.join(assets, name), encoding="utf-8") as fh:
            s.add_artifact(tid10, path=f(name, fh.read()), title=title, category="visual")
    clk.at(102)
    s.add_log(tid10, "시각화: 요약 + 도식 2개 — V1 두 자료 → 왜도 비교 → 결론 (Parallel Dual-Stream), "
                     "V2 원고 다섯 부분의 주장 (Linear Pipeline). 결론의 'independent of the Reynolds number' 와 "
                     "방법의 Re_τ = 180 하나를 나란히 볼 수 있게 했습니다.")
    s.update_task(tid10, step="리뷰 리포트 확인을 기다리는 중")

    # ---------------------------------------------------------------- T-0011 제안서(Word) 문서 리뷰 (완료)
    clk.at(1500)
    t11 = s.create_task(
        "문서 리뷰: 계산 자원 제안서 초안 (Word)", type="docreview", agent="Claude",
        instruction="- 검토할 문서: ~/Documents/proposal_draft.docx\n- 문서 종류: 제안서\n"
                    "- 문서의 목적 · 제출처: HPC 계산 시간 신청 (다음 주 마감)\n\n심사위원 입장에서 봐 주세요.",
        plan=["문서 읽기 · 요약", "목적 대비 미비한 부분 · 논리", "문장 · 맞춤법", "리뷰 리포트(PDF) 작성"],
        tags=["proposal"])
    tid11 = t11["id"]
    clk.at(1495)
    s.add_artifact(tid11, path=f("proposal_draft.docx", simple_docx(PROPOSAL_DOCX, PROPOSAL_FOOTNOTES), "wb"),
                   title="검토할 문서: proposal_draft.docx", category="source")
    s.update_task(tid11, step="제안서 읽기 · 요약", progress=15)
    clk.at(1480); s.check_plan(tid11, [1])
    clk.at(1470); s.check_plan(tid11, [2])
    clk.at(1462); s.check_plan(tid11, [3])
    clk.at(1450)
    s.set_review(tid11, PROPOSAL_REVIEW)
    s.add_artifact(tid11, path=f("doc-review-proposal.md", PROPOSAL_REPORT.format(date=today)),
                   title="문서 리뷰 리포트 (Markdown)", category="review")
    rep11 = s.add_artifact(tid11, path=f("doc-review-proposal.pdf", simple_pdf("Document Review - Compute proposal", [
        "Draft proposal for HPC compute time (2 pages, Word)", "",
        "## Bottom line",
        "Well structured, but the 50,000 node-hour request has no basis (R1) and",
        "goal 2 (data-driven wall model) has no method (R2). Verdict: minor revision.", "",
        "## Spelling (Korean)",
        "R4-R6  three spelling and spacing fixes",
    ]), "wb"), title="문서 리뷰 리포트 (PDF)", category="review")
    clk.at(1446); s.check_plan(tid11, [4])
    ap11 = s.request_approval(tid11, "제안서 리뷰 리포트를 확인해 주세요", kind="result", artifact_ids=[rep11["id"]],
                              description="수정 권장 2개(R1 노드시간 근거, R2 벽 모델 방법)와 맞춤법 3곳입니다.")
    clk.at(1400)
    s.decide(tid11, ap11["id"], "approve", comment="R1 은 지난 할당량 사용 실적으로 보완하겠습니다. 고맙습니다.")
    s.ack_decision(tid11, ap11["id"])
    clk.at(1395); s.update_task(tid11, status="done", message="리뷰 리포트 전달 완료 (소폭 수정)")

    # ---------------------------------------------------------------- T-0006 ↔ T-0012 Codex 리뷰 반복 → 커밋 승인 대기
    if repo:
        _git(repo, "mv", "src/pressure_solver.cu", "src/pressure_solver.hip")
        _write(os.path.join(repo, "src", "pressure_solver.hip"), SOLVER_V2)
        _write(os.path.join(repo, "src", "hip_utils.h"), HIP_UTILS)
        _write(os.path.join(repo, "CMakeLists.txt"), CMAKE_V2)
        # CMake 빌드 폴더 (.gitignore 에 없음) — diff · 커밋에서 빠지는 것을 보여 준다
        _write(os.path.join(repo, "build", "CMakeCache.txt"), "CMAKE_BUILD_TYPE:STRING=Release\n")
        _write(os.path.join(repo, "build", "CMakeFiles", "cfd.dir", "src", "main.cpp.o"), "obj\n")
        _write(os.path.join(repo, "build", "compile_commands.json"), "[]\n")
        with open(os.path.join(repo, "build", "cfd"), "wb") as fh:
            fh.write(b"\x7fELF\x02\x01\x01" + b"\x00" * 57)
        clk.at(52)
        r1 = s.review_request(tid6, author="Claude", note="커널 · 메모리 전송 · 오류 처리 위주로 봐 주세요")
        rtid = r1["task"]
        clk.at(50); s.update_task(rtid, status="in_progress", step="1차 리뷰: 바뀐 파일 3개 읽는 중", progress=30,
                                  author="Codex", claim="Codex")
        clk.at(44)
        s.set_review(rtid, {"findings": [
            {"id": "R1", "severity": "major", "title": "커널 실행 뒤 오류 확인 없음",
             "plain": "GPU 계산이 실패해도 알아차리지 못하고, 덜 계산된 값을 결과로 씁니다.",
             "file": "src/pressure_solver.hip", "line": 31,
             "suggestion": "반복문이 끝난 뒤 `HIP_CHECK(hipGetLastError());` 를 넣으세요."},
            {"id": "R2", "severity": "minor", "title": "블록 크기 64×4 가 두 곳에 숫자로 적힘",
             "plain": "블록 크기를 바꿀 때 한 곳만 고치면 격자 일부가 계산되지 않습니다.",
             "file": "src/pressure_solver.hip", "line": 27,
             "suggestion": "`constexpr int BX = 64, BY = 4;` 로 한 번만 적고 grid 계산에도 쓰세요."},
        ]}, author="Codex")
        clk.at(43)
        s.review_done(rtid, verdict="changes", summary="포팅 방향은 맞습니다. 커널 오류 확인(R1)을 넣어 주세요.",
                      author="Codex")
        s.ack_inbox(s.inbox(agent="Codex", task=rtid))
        clk.at(36); s.ack_inbox(s.inbox(agent="Claude", task=tid6))
        s.update_task(tid6, step="리뷰 R1 · R2 반영: 커널 오류 확인, 블록 크기 상수", author="Claude")
        _write(os.path.join(repo, "src", "pressure_solver.hip"), SOLVER_V3)
        clk.at(30)
        s.review_request(tid6, author="Claude", note="R1 · R2 고쳤습니다")
        clk.at(24)
        s.set_review(rtid, {"findings": [{"id": "R1", "status": "fixed"}, {"id": "R2", "status": "fixed"}]},
                     author="Codex")
        s.ack_inbox(s.inbox(agent="Codex", task=rtid))
        clk.at(23)
        s.review_done(rtid, verdict="approve", summary="R1 · R2 모두 고쳐졌습니다. 커밋해도 좋습니다.", author="Codex")
        clk.at(20); s.ack_inbox(s.inbox(agent="Claude", task=tid6))
        clk.at(8)
        s.request_approval(
            tid6, "HIP 포팅 변경사항 커밋 · 푸시", artifact_ids=[perf["id"]],
            description="수치 검증(상대오차 3.2e-13)과 성능 측정을 마쳤고, Codex 리뷰 2차에서 통과했습니다. 승인해 주시면 "
                        "`feature/hip-pressure` 브랜치에 커밋하겠습니다.\n\n"
                        "> 데모 저장소에는 원격이 없어 푸시는 생략됩니다. 승인 후 `devtrack --data <dir> apply T-0006 A-1` 로 실제 커밋을 확인할 수 있습니다.",
            commit={"repo": repo, "message": "Port pressure solver to HIP for MI250X\n\n"
                                             "- hipify kernels, 64x4 blocks for 64-wide wavefronts\n"
                                             "- add HIP_CHECK error macro and check kernel launches\n"
                                             "- fix missing stream sync before D2H copy",
                    "push": False})

    # ---------------------------------------------------------------- T-0005 ↔ T-0013 저널 심사 반복 (1차 대폭 수정 → 2차 소폭 수정)
    paper = os.path.join(s.root, ".repos", "jfm-paper")
    _write(os.path.join(paper, "main.tex"), PAPER_MAIN)
    _write(os.path.join(paper, "chapters", "results.tex"), RESULTS_TEX)
    _write(os.path.join(paper, "refs.bib"), PAPER_BIB)
    clk.at(48)
    p1 = s.review_request(tid5, author="Claude", note="4장 결과 섹션 초안입니다. 수치 근거 위주로 봐 주세요.")
    ptid = p1["task"]
    clk.at(46); s.update_task(ptid, status="in_progress", step="1차 심사: 원고 읽는 중", progress=30, author="Codex",
                              claim="Codex")
    clk.at(40)
    s.set_review(ptid, {"summary": "The port is useful, but the accuracy and performance claims need firmer support "
                                   "before the paper can be considered further.",
                        "findings": [
        {"id": "R1", "severity": "blocker", "location": "Section 4.1, eq. (4.1)",
         "quote": "the observed order of accuracy defined in \\eqref{eq:l2} is $p = 2.01$",
         "title": "Observed order from only two grids",
         "plain": "두 격자만으로 정확도 차수를 계산해서, 2차 정확도라는 주장을 믿기 어렵습니다.",
         "suggestion": "Use at least three grids, report Richardson extrapolation and the grid convergence index (GCI)."},
        {"id": "R2", "severity": "major", "location": "Section 4.2, Table 4.1", "title": "Timings without variation",
         "plain": "속도 향상 수치에 반복 측정의 편차가 없어, 112배가 우연인지 알 수 없습니다.",
         "suggestion": "Give mean and spread over repeated runs and state how the CPU baseline was compiled."},
        {"id": "R3", "severity": "minor", "location": "Figure 4.1", "title": "Missing second-order reference line",
         "plain": "그림에 2차 기준선이 없어 기울기를 눈으로 확인하기 어렵습니다.",
         "suggestion": "Add an O(h^2) reference line next to the first-order one."},
    ]}, author="Codex")
    clk.at(39)
    s.review_done(ptid, verdict="major", summary="정확도 · 성능 주장의 근거를 보강해야 합니다 (R1 · R2).", author="Codex")
    s.ack_inbox(s.inbox(agent="Codex", task=ptid))
    clk.at(34); s.ack_inbox(s.inbox(agent="Claude", task=tid5))
    s.update_task(tid5, step="심사 R1 · R2 반영: 3개 격자 GCI, Table 4.1 반복 측정", author="Claude")
    _write(os.path.join(paper, "chapters", "results.tex"), RESULTS_TEX.replace(
        "is $p = 2.01$, confirming the second-order spatial\ndiscretisation.",
        "is $p = 2.01$ on the three finest grids (Richardson extrapolation, GCI $= 0.4\\%$ following\n"
        "\\citet{roache1994}), confirming the second-order spatial discretisation.").replace(
        "achieves a $112\\times$", "achieves a $112.3\\times$ (mean of five runs, $\\pm 1.2\\%$)"))
    _write(os.path.join(paper, "response_round2.md"), PAPER_RESPONSE)
    clk.at(28)
    s.review_request(tid5, author="Claude", response=os.path.join(paper, "response_round2.md"),
                     note="R1 · R2 반영, R3 은 다음 판에서")
    clk.at(18)
    s.set_review(ptid, {"findings": [{"id": "R1", "status": "fixed"}, {"id": "R2", "status": "fixed"}]},
                 author="Codex")
    s.ack_inbox(s.inbox(agent="Codex", task=ptid))
    clk.at(17)
    s.review_done(ptid, verdict="minor", summary="R1 · R2 는 해결됐습니다. 그림 기준선(R3)만 남았습니다.", author="Codex")
    clk.at(15); s.ack_inbox(s.inbox(agent="Claude", task=tid5))

    # 발표 자료 · 포스터 (/make-slides · /make-poster → devtrack pptx, 표준 라이브러리) — 원고 4장의 숫자 · 그림만 씀
    clk.at(12)
    s.add_user_comment(tid5, "그룹 미팅용 10분 발표 자료(pptx)와 A0 포스터도 만들어 주세요 (/make-slides, /make-poster)")
    clk.at(11); s.ack_inbox(s.inbox(task=tid5))
    s.update_task(tid5, step="발표 자료 · 포스터: 원고 4장의 그림 · 표로 구성")
    from . import pptx as _pptx
    fig41, fig42 = os.path.join(tmp, "fig4_1_convergence.svg"), os.path.join(tmp, "fig4_2_speedup.svg")
    table = {"header": ["Platform", "Time/step [ms]", "Speedup"],
             "rows": [["CPU (64 cores)", "412.0", "1.0"], ["A100", "22.4", "18.4"], ["MI250X (1 GCD)", "25.9", "15.9"],
                      ["MI250X (8 GCD)", "3.67", "112.3"]], "highlight": [[4, 2]]}
    deck = {"title": "A portable GPU pressure solver — results", "numbers": True,
            "footer": "Pressure solver HIP port · Results (Ch. 4)", "slides": [
                {"layout": "title", "title": "A portable GPU pressure solver for incompressible flow",
                 "subtitle": "Chapter 4: accuracy, performance and physical fidelity",
                 "lines": ["[[MISSING: presenter · group meeting date]]"],
                 "note": "Ten minutes, three questions from Chapter 4."},
                {"layout": "figure-bullets", "title": "Second-order accurate: observed order p = 2.01", "image": fig41,
                 "caption": "Relative L_{2} error vs grid spacing (Fig. 4.1)",
                 "bullets": ["Three finest grids, Richardson extrapolation", "GCI = 0.4 % (Roache 1994)",
                             "Double precision, Δτ = 10^{-4}"], "note": "Eq. 4.1; GCI after Roache 1994. ~1.5 min."},
                {"layout": "table", "title": "8 MI250X GCDs: 3.67 ms per step, 112.3× the CPU", "table": table,
                 "caption": "Wall-clock time per step on a 2048² grid (Table 4.1); mean of five runs, ±1.2 %",
                 "note": "Same grid on every platform. ~1.5 min."},
                {"layout": "figure-bullets", "title": "Why it is fast", "image": fig42, "image_side": "right",
                 "caption": "Speedup relative to the 64-core CPU baseline (Fig. 4.2)",
                 "bullets": ["64×4 thread blocks for 64-wide wavefronts", "Memory-bound: 1.31 TB/s (82 % of peak)",
                             "Transfers overlapped with HIP streams"], "note": "The three factors of Section 4.2."},
                {"layout": "statement", "title": "Take-home", "text": "One HIP code: second-order accurate and 112× faster",
                 "sub": "Next: strong/weak scaling and the final turbulence statistics", "note": "~1 min."}]}
    poster = {"title": "A portable GPU pressure solver", "size": "A0", "slides": [{
        "layout": "poster", "title": "A portable GPU pressure solver for incompressible flow",
        "authors": "[[MISSING: authors]]", "affiliation": "[[MISSING: affiliations]]", "text_size": 40, "heading_size": 60,
        "columns": [
            [{"heading": "Three checks for the HIP port",
              "bullets": ["**Accuracy**: grid convergence against Taylor–Green", "**Performance**: speedup over a 64-core CPU",
                          "**Physics**: turbulence statistics vs wind tunnel"]},
             {"heading": "Second-order accurate", "image": fig41, "caption": "Relative L_{2} error vs grid spacing (Fig. 4.1)"},
             {"heading": "Observed order", "text": "p = 2.01 on the three finest grids, GCI = 0.4 % (Roache 1994)."}],
            [{"heading": "112.3× faster on 8 GCDs", "image": fig42, "caption": "Speedup vs the 64-core CPU (Fig. 4.2)"},
             {"heading": "Time per step, 2048² grid", "table": table},
             {"heading": "Take-home", "bullets": ["One HIP code for MI250X and A100", "Second-order accurate (p = 2.01)",
                                                  "112.3× the 64-core CPU on 8 GCDs"]}]]}]}
    clk.at(8)
    for name, sp, title in (("deck.pptx", deck, "발표 자료 (PowerPoint, 10분)"), ("poster.pptx", poster, "포스터 (PowerPoint, A0)")):
        out = os.path.join(tmp, name)
        _pptx.write_pptx(sp, out, tmp, svg_fallback=False)
        s.add_artifact(tid5, path=out, title=title)
    s.add_log(tid5, "발표 자료 5장 · A0 포스터(2단) — 원고 4장의 그림 · 표 · 숫자만 사용, 발표자 · 저자 정보는 [[MISSING]] 으로 둠")
