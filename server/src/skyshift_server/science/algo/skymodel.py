"""Spectral-aware static-sky model ("subtract the known sky").

Every SPHEREx frame of a given patch is taken at a different wavelength (the linear
variable filter maps wavelength onto detector position), so naive frame-to-frame
subtraction leaves the colour of every star behind. Instead we fit, independently for
every pixel, a smooth spectrum I_p(lambda) to all frames at once (robust, inverse-variance
weighted B-spline in log-lambda). The model is the static sky at each frame's wavelength;
the residual cube r_p(t) = I_p(t) - model_p(lambda_t) contains only what changes:
moving objects (positive at the new position, negative at the old one), variable stars,
and artefacts.

The fit is vectorised over pixels: for K basis functions and P pixels we solve P small
K x K normal-equation systems in one einsum.
"""
import numpy as np

LAM_MIN, LAM_MAX = 0.73, 5.05


def bspline_basis(lam, n_knots=12, degree=3):
    """B-spline design matrix in log-lambda across the full SPHEREx range."""
    x = np.log(np.asarray(lam, float))
    lo, hi = np.log(LAM_MIN), np.log(LAM_MAX)
    inner = np.linspace(lo, hi, n_knots)
    t = np.r_[[lo] * degree, inner, [hi] * degree]
    nb = len(t) - degree - 1
    B = np.zeros((len(x), nb))
    xx = np.clip(x, lo, hi - 1e-9)
    # Cox-de Boor
    N = np.zeros((len(x), len(t) - 1))
    for i in range(len(t) - 1):
        N[:, i] = (xx >= t[i]) & (xx < t[i + 1])
    for k in range(1, degree + 1):
        Nn = np.zeros((len(x), len(t) - 1 - k))
        for i in range(len(t) - 1 - k):
            d1 = t[i + k] - t[i]
            d2 = t[i + k + 1] - t[i + 1]
            a = (xx - t[i]) / d1 * N[:, i] if d1 > 0 else 0
            b = (t[i + k + 1] - xx) / d2 * N[:, i + 1] if d2 > 0 else 0
            Nn[:, i] = a + b
        N = Nn
    B[:] = N[:, :nb]
    return B


def fit_static_sky(cube, var, lam, valid=None, n_knots=12, ridge=1e-3, n_iter=4, clip=3.0,
                   frame_weight=None):
    """Robust per-pixel spectral fit.

    cube, var : (T, Y, X) arrays (NaN = missing)
    lam       : (T,) wavelength of each frame at the target [micron]
    returns model (T, Y, X), residual (T, Y, X), coefficients (K, Y, X), chi (T, Y, X)
    """
    T, Y, X = cube.shape
    A = bspline_basis(lam, n_knots)                         # (T, K)
    K = A.shape[1]
    # drop basis functions that no frame constrains
    used = A.sum(0) > 1e-6
    A = A[:, used]
    K = A.shape[1]
    d = cube.reshape(T, -1)
    v = var.reshape(T, -1)
    ok = np.isfinite(d) & np.isfinite(v) & (v > 0)
    if valid is not None:
        ok &= valid.reshape(T, -1)
    w = np.where(ok, 1.0 / np.where(ok, v, 1), 0.0)
    if frame_weight is not None:
        w *= np.asarray(frame_weight)[:, None]
    dd = np.where(ok, d, 0.0)
    # regularise toward a smooth spectrum: second-difference penalty on coefficients
    D2 = np.diff(np.eye(K), 2, axis=0)
    R = ridge * (D2.T @ D2)
    for it in range(n_iter):
        ATA = np.einsum("tk,tl,tp->pkl", A, A, w)           # (P, K, K)
        scale = np.trace(ATA, axis1=1, axis2=2)[:, None, None] / K + 1e-30
        ATA = ATA + R[None] * scale
        ATb = np.einsum("tk,tp->pk", A, w * dd)
        coef = np.linalg.solve(ATA, ATb[..., None])[..., 0]  # (P, K)
        model = A @ coef.T                                   # (T, P)
        chi = (d - model) * np.sqrt(np.where(ok, 1.0 / np.where(ok, v, 1), 0))
        # robust re-weighting: Tukey-ish down-weighting of outliers (movers, CRs)
        wt = np.where(np.abs(chi) > clip, (clip / np.maximum(np.abs(np.where(ok, chi, 1)), 1e-12)) ** 2, 1.0)
        w = np.where(ok, wt / np.where(ok, v, 1), 0.0)
        if frame_weight is not None:
            w *= np.asarray(frame_weight)[:, None]
    resid = np.where(ok, d - model, np.nan)
    chi = np.where(ok, chi, np.nan)
    return (model.reshape(T, Y, X), resid.reshape(T, Y, X), coef.T.reshape(K, Y, X), chi.reshape(T, Y, X))


def reference_image(model, lam, lam0):
    """Static-sky image evaluated at any wavelength (pick frame with nearest wavelength)."""
    i = int(np.argmin(np.abs(np.asarray(lam) - lam0)))
    return model[i]
