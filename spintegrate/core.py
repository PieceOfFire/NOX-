"""Numerical pipeline, independent of the desktop interface.

Widths are HWHM in ppm. Optional bounded center refinement and pseudo-Voigt
profiles extend the original Lorentzian model. Historical output is area/pi.
"""
from __future__ import annotations

import csv
import json
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import nmrglue as ng
import numpy as np
from pybaselines import Baseline
from scipy.optimize import least_squares
from .profiles import LINE_SHAPES, model_areas, model_components


BASELINE_METHODS = ('none', 'asls', 'arpls', 'airpls', 'drpls', 'iasls',
                    'snip', 'modpoly', 'imodpoly', 'mor')
PHASE_MODES = ('none', 'acme', 'peak_minima', 'abs')


@dataclass
class BaselineSettings:
    method: str = 'snip'
    lam: float = 1e6
    p: float = .01
    max_iter: int = 50
    tol: float = .001
    half_window: int = 30
    smooth_half_window: int = 3
    poly_order: int = 3


@dataclass
class ProcessingSettings:
    phase: str = 'none'
    abs_source: str = 'spectrum'  # 'fid': literal abs(FID) BEFORE FFT
    lb_hz: float = 1.0
    p0: float = 0.0
    p1: float = 0.0
    phase_peak_width: int = 100
    reference_mode: str = 'metadata'  # metadata / strongest / window
    reference_ppm: float = 7.15
    reference_low: float = 6.8
    reference_high: float = 7.5
    ppm_offset: float = 0.0
    zero_fill_factor: int = 1
    remove_digital_filter: bool = True


@dataclass
class FitSettings:
    threshold: float = 1000.0
    min_separation_points: int = 2
    signed: bool = False
    initial_width_hz: float = 1.0  # fallback FWHM if lb_hz=0
    min_width_hz: float = .05
    max_width_hz: float = 50.0
    max_nfev: int = 2000
    restarts: int = 3
    max_peaks: int = 150
    loss: str = 'linear'
    residual_warning: float = .15
    cancellation_warning: float = .7
    line_shape: str = 'lorentzian'
    refine_centers: bool = False
    max_center_shift_hz: float = 2.0
    component_shapes: list[dict] = field(default_factory=list)
    max_asymmetry_ratio: float = 3.0
    max_profile_phase_degrees: float = 15.0
    peak_detection: str = 'prominence'
    min_prominence: float = 0.0  # 0: threshold / estimated noise, not an absolute offset


@dataclass
class FitRegion:
    name: str
    low: float
    high: float
    baseline: BaselineSettings = field(default_factory=BaselineSettings)


@dataclass
class IntegrationRegion:
    name: str
    parent: str
    low: float
    high: float


@dataclass
class Session:
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)
    fit: FitSettings = field(default_factory=FitSettings)
    baseline_default: BaselineSettings = field(default_factory=BaselineSettings)
    regions: list[FitRegion] = field(default_factory=list)
    integrals: list[IntegrationRegion] = field(default_factory=list)
    schema_version: int = 1

    def validate(self, require_regions: bool = False):
        p, f = self.processing, self.fit
        if p.phase not in PHASE_MODES or p.abs_source not in ('fid', 'spectrum'):
            raise ValueError('Неизвестный режим фазирования/abs.')
        if p.reference_mode not in ('metadata', 'strongest', 'window'):
            raise ValueError('Неизвестный режим привязки ppm.')
        if not np.isfinite(p.lb_hz) or p.lb_hz < 0:
            raise ValueError('Аподизация LB должна быть >= 0 Гц.')
        if p.zero_fill_factor not in (1, 2, 4, 8):
            raise ValueError('Zero filling: 1, 2, 4 или 8.')
        if p.phase_peak_width < 1:
            raise ValueError('Окно peak_minima должно быть положительным.')
        if p.reference_mode == 'window' and p.reference_low >= p.reference_high:
            raise ValueError('Неверное окно референса.')
        if not (np.isfinite(f.threshold) and f.threshold > 0):
            raise ValueError('Threshold должен быть положительным.')
        if not (0 < f.min_width_hz < f.max_width_hz) or f.initial_width_hz <= 0:
            raise ValueError('Ширины: 0 < min < max; initial > 0.')
        if f.max_nfev < 1 or f.restarts < 1 or f.max_peaks < 1 or f.min_separation_points < 1:
            raise ValueError('Лимиты оптимизации и число точек должны быть >= 1.')
        if f.loss not in ('linear', 'soft_l1'):
            raise ValueError('Loss: linear или soft_l1.')
        if f.line_shape not in LINE_SHAPES:
            raise ValueError('Неизвестная форма линии.')
        if f.peak_detection not in ('prominence', 'height'):
            raise ValueError('Неизвестный способ поиска линий.')
        if not np.isfinite(f.min_prominence) or f.min_prominence < 0:
            raise ValueError('Минимальная выраженность должна быть >= 0 (0 — авто).')
        if type(f.refine_centers) is not bool:
            raise ValueError('Уточнение центров должно быть включено или выключено.')
        if not np.isfinite(f.max_center_shift_hz) or f.max_center_shift_hz <= 0:
            raise ValueError('Допустимое смещение центра должно быть > 0 Гц.')
        if (not np.isfinite([f.max_asymmetry_ratio, f.max_profile_phase_degrees]).all()
                or not 1 < f.max_asymmetry_ratio <= 10 or not 0 < f.max_profile_phase_degrees <= 30):
            raise ValueError('Асимметрия: 1 < отношение <= 10; фаза модели: 0 < угол <= 30°.')
        if not isinstance(f.component_shapes, list):
            raise ValueError('Переопределения формы должны быть списком.')
        for override in f.component_shapes:
            if (not isinstance(override, dict) or override.get('shape') not in LINE_SHAPES
                    or not isinstance(override.get('center'), (float, int))
                    or not np.isfinite(override['center'])
                    or not isinstance(override.get('tolerance', .005), (float, int))
                    or not np.isfinite(override.get('tolerance', .005))
                    or override.get('tolerance', .005) <= 0):
                raise ValueError('Некорректная форма / привязка компоненты.')
        if not 0 < f.cancellation_warning < 1 or f.residual_warning <= 0:
            raise ValueError('Неверные пороги диагностики.')
        if require_regions and (not self.regions or not self.integrals):
            raise ValueError('Сохраните области обработки и интегрирования.')
        names = [r.name for r in self.regions]
        if len(names) != len(set(names)) or any(not n.strip() for n in names):
            raise ValueError('Имена областей обработки должны быть уникальны.')
        ordered = sorted(self.regions, key=lambda r: r.low)
        for r in ordered:
            if not np.isfinite([r.low, r.high]).all() or r.low >= r.high:
                raise ValueError(f'{r.name}: нужны две различные конечные границы.')
            validate_baseline(r.baseline)
        intnames = [r.name for r in self.integrals]
        if len(intnames) != len(set(intnames)) or any(not n.strip() for n in intnames):
            raise ValueError('Имена интегралов должны быть уникальны.')
        parents = {r.name: r for r in self.regions}
        for r in self.integrals:
            parent = parents.get(r.parent)
            if (parent is None or not np.isfinite([r.low, r.high]).all()
                    or not parent.low <= r.low < r.high <= parent.high):
                raise ValueError(f'{r.name}: область интегрирования должна лежать внутри {r.parent}.')
        validate_baseline(self.baseline_default)
        numeric = [p.p0, p.p1, p.reference_ppm, p.reference_low, p.reference_high,
                   p.ppm_offset, f.initial_width_hz, f.min_width_hz, f.max_width_hz]
        if not np.isfinite(numeric).all():
            raise ValueError('Настройки должны содержать конечные числа.')

    def save(self, path):
        self.validate()
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding='utf-8')

    @classmethod
    def load(cls, path):
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        if d.get('schema_version') != 1:
            raise ValueError('Неподдерживаемая версия настроек.')
        session = cls(
            processing=ProcessingSettings(**d['processing']), fit=FitSettings(**d['fit']),
            baseline_default=BaselineSettings(**d['baseline_default']),
            regions=[FitRegion(**{**r, 'baseline': BaselineSettings(**r['baseline'])}) for r in d['regions']],
            integrals=[IntegrationRegion(**r) for r in d['integrals']])
        session.validate()
        return session


def validate_baseline(b: BaselineSettings):
    if b.method not in BASELINE_METHODS:
        raise ValueError(f'Неизвестный метод базовой линии: {b.method}')
    if (not np.isfinite([b.lam, b.p, b.tol]).all() or b.lam <= 0 or not 0 < b.p < 1
            or b.tol <= 0 or b.max_iter < 1 or b.half_window < 1
            or b.smooth_half_window < 0 or b.poly_order < 0):
        raise ValueError('Некорректные параметры базовой линии.')


@dataclass
class RawSpectrum:
    fid: np.ndarray
    sf_mhz: float
    sw_hz: float
    carrier_ppm: float
    source: str
    notes: list[str] = field(default_factory=list)


@dataclass
class Spectrum:
    ppm: np.ndarray  # ascending internally; invert only the displayed axis
    intensity: np.ndarray
    complex_spectrum: np.ndarray
    sf_mhz: float
    sw_hz: float
    source: str
    phases: tuple[float, float]
    notes: list[str]


def load_raw(path, remove_digital_filter=True) -> RawSpectrum:
    path = Path(path)
    notes = []
    if path.suffix.lower() == '.npz':
        with np.load(path, allow_pickle=False) as data:
            fid = np.asarray(data['fid'], dtype=complex)
            sf, sw = float(data['sf_mhz']), float(data['sw_hz'])
            carrier = float(data['carrier_ppm'])
    else:
        dic, fid = ng.bruker.read(str(path))
        if np.asarray(fid).ndim != 1:
            raise ValueError('Поддерживаются только одномерные Bruker FID.')
        acq = dic['acqus']
        sf, sw = float(acq['SFO1']), float(acq['SW_h'])
        carrier = float(acq['O1']) / sf
        if remove_digital_filter:
            # Skip only an explicitly absent filter; malformed metadata must not be hidden.
            if float(acq.get('GRPDLY', 0)) > 0 or int(acq.get('DECIM', 1)) > 1:
                fid = ng.bruker.remove_digital_filter(dic, fid)
            else:
                notes.append('Цифровой фильтр отсутствует по метаданным; удаление пропущено.')
        else:
            notes.append('Удаление цифрового фильтра отключено.')
    fid = np.asarray(fid, dtype=complex)
    if fid.ndim != 1 or fid.size < 16 or not np.isfinite(fid).all():
        raise ValueError('Нужен конечный одномерный FID длиной >= 16 точек.')
    if not np.isfinite([sf, sw, carrier]).all() or sf <= 0 or sw <= 0:
        raise ValueError('Неверные SFO1/SW_h/O1 в метаданных.')
    return RawSpectrum(fid, sf, sw, carrier, str(path), notes)


def process(raw: RawSpectrum, p: ProcessingSettings) -> Spectrum:
    Session(processing=p).validate()
    fid = raw.fid.copy()
    if p.phase == 'abs' and p.abs_source == 'fid':
        fid = np.abs(fid).astype(complex)
    # nmrglue expects normalized LB; the UI and JSON use physical Hz.
    fid = ng.proc_base.em(fid, lb=p.lb_hz / raw.sw_hz)
    if p.zero_fill_factor > 1:
        fid = ng.proc_base.zf_size(fid, raw.fid.size * p.zero_fill_factor)
    ft = ng.proc_base.fft(fid)
    phases = (0., 0.)
    notes = list(raw.notes)
    if p.phase in ('acme', 'peak_minima'):
        # Both built-in scoring modes are evaluated by nmrglue's autops.
        counts = {'evaluations': 0, 'iterations': 0}
        peak_width = min(p.phase_peak_width, max(1, len(ft)//4))
        def score(phases, data):
            counts['evaluations'] += 1
            if p.phase == 'acme':
                return ng.proc_autophase._ps_acme_score(phases, data)
            # The built-in peak_minima scorer assumes a peak away from either
            # edge. Give a readable failure rather than silently alter the data.
            try:
                return ng.proc_autophase._ps_peak_minima_score(phases, data, peak_width)
            except ValueError as exc:
                raise ValueError('peak_minima: недостаточно точек около максимума. '
                                 'Уменьшите окно peak_minima или выберите ACME.') from exc
        def iteration(_):
            counts['iterations'] += 1
        with warnings.catch_warnings(record=True) as caught:
            phased, optimized = ng.proc_autophase.autops(
                ft, score, p0=p.p0, p1=p.p1, return_phases=True,
                disp=False, maxiter=1000, maxfun=2000, ftol=1e-6, callback=iteration)
        phases = tuple(float(v) for v in optimized)
        y = phased.real
        notes.extend(str(w.message) for w in caught)
        if counts['evaluations'] >= 2000 or counts['iterations'] >= 999:
            notes.append('Автокоррекция фазы достигла лимита оптимизации; проверьте фазу вручную.')
    elif p.phase == 'abs' and p.abs_source == 'spectrum':
        y = np.abs(ft)
        notes.append('abs спектра: форма magnitude-пика отличается от чистого Лоренца.')
    else:
        y = ft.real
        if p.phase == 'abs':
            notes.append('abs(FID) до FFT: фазовая/частотная информация FID потеряна.')
    # ng.fft implements fftshift(fft). The original notebook assigns increasing
    # frequencies to these bins. Keep that orientation, with exact bin spacing
    # instead of endpoint-inclusive linspace; sort/invert only for display.
    frequencies = np.fft.fftshift(np.fft.fftfreq(len(ft), d=1/raw.sw_hz))
    ppm = raw.carrier_ppm + frequencies / raw.sf_mhz
    if p.reference_mode != 'metadata':
        mask = np.ones(len(ppm), dtype=bool)
        if p.reference_mode == 'window':
            mask = (ppm >= p.reference_low) & (ppm <= p.reference_high)
            if not mask.any():
                raise ValueError('Окно референса находится вне спектра.')
        indexes = np.flatnonzero(mask)
        # Use magnitude for a stable reference, independently of phase choice.
        index = indexes[np.argmax(np.abs(ft[indexes]))]
        ppm = ppm + p.reference_ppm - ppm[index]
        if p.reference_mode == 'strongest':
            notes.append('Референс = самый сильный пик: проверяйте идентичность сигнала в серии.')
    ppm = ppm + p.ppm_offset
    order = np.argsort(ppm)
    if not np.isfinite(y).all():
        raise ValueError('После обработки появились нечисловые значения.')
    return Spectrum(ppm[order], np.asarray(y)[order], ft[order], raw.sf_mhz,
                    raw.sw_hz, raw.source, phases, notes)


def baseline_correct(x, y, b: BaselineSettings):
    validate_baseline(b)
    x, y = np.asarray(x), np.asarray(y)
    if len(x) < 8 or not np.all(np.diff(x) > 0):
        raise ValueError('Область должна содержать >= 8 точек с возрастающей ppm-шкалой.')
    if b.method == 'none':
        return y.copy(), np.zeros_like(y), []
    fitter = Baseline(x_data=x, check_finite=True)
    whittaker = dict(lam=b.lam, max_iter=b.max_iter, tol=b.tol)
    if b.method in ('asls', 'iasls'):
        kwargs = {**whittaker, 'p': b.p}
    elif b.method in ('arpls', 'airpls', 'drpls'):
        kwargs = whittaker
    elif b.method == 'snip':
        kwargs = dict(max_half_window=min(b.half_window, (len(x)-1)//2),
                      decreasing=True, smooth_half_window=min(b.smooth_half_window, (len(x)-1)//2),
                      pad_kwargs={'mode': 'edge'})
    elif b.method in ('modpoly', 'imodpoly'):
        kwargs = dict(poly_order=min(b.poly_order, len(x)-2), max_iter=b.max_iter, tol=b.tol)
    else:
        kwargs = dict(half_window=min(b.half_window, (len(x)-1)//2))
    # Baseline algorithms operate on normalized intensities for numeric stability.
    scale = max(float(np.max(np.abs(y))), np.finfo(float).tiny)
    with warnings.catch_warnings(record=True) as caught:
        line, params = getattr(fitter, b.method)(y / scale, **kwargs)
    line = np.asarray(line) * scale
    if not np.isfinite(line).all():
        raise ValueError('Коррекция базовой линии вернула нечисловые значения.')
    notes = [str(w.message) for w in caught]
    history = params.get('tol_history')
    if history is not None and len(history) and np.asarray(history).ravel()[-1] > b.tol:
        notes.append(f'{b.method}: базовая линия не достигла tol={b.tol:g}.')
    return y - line, line, notes


def pick_peaks(x, y, f: FitSettings):
    if f.peak_detection == 'prominence':
        from .peak_detection import detect_peaks
        indices, _ = detect_peaks(y, f.threshold, f.min_separation_points,
                                 signed=f.signed, prominence=f.min_prominence)
        return np.asarray(x)[indices], np.asarray(y)[indices]
    locations = ng.peakpick.pick(np.asarray(y), pthres=f.threshold,
                            nthres=-f.threshold if f.signed else None,
                            algorithm='downward', msep=[f.min_separation_points],
                            est_params=False, cluster=False, table=False)
    if not locations:
        return np.array([], dtype=float), np.array([], dtype=float)
    indices = np.unique(np.clip(np.rint(np.asarray(locations)[:, 0]).astype(int), 0, len(x)-1))
    # Downward picking does not apply msep in all nmrglue versions. Explicitly
    # enforce it by retaining the strongest local extrema first.
    chosen = []
    for i in sorted(indices, key=lambda i: abs(y[i]), reverse=True):
        if all(abs(i-j) >= f.min_separation_points for j in chosen):
            chosen.append(i)
    indices = np.array(sorted(chosen), dtype=int)
    return np.asarray(x)[indices], np.asarray(y)[indices]


def lorentzian(x, center, width, amplitude):
    return amplitude * width**2 / ((np.asarray(x) - center)**2 + width**2)


def peak_profile(x, center, width, amplitude, mixing=1.0):
    """Height-normalized pseudo-Voigt; mixing=1 is Lorentzian, 0 Gaussian."""
    mixing = np.asarray(mixing)
    u2 = ((np.asarray(x) - center) / width)**2
    return amplitude * (mixing / (1 + u2) + (1 - mixing) * np.exp(-np.log(2) * u2))


def peak_area(amplitude, width, mixing=1.0):
    """Signed full-line area, using the same HWHM for both profile terms."""
    return np.asarray(amplitude) * np.asarray(width) * (
        np.asarray(mixing) * np.pi + (1 - np.asarray(mixing)) * np.sqrt(np.pi / np.log(2))
    )


def r_squared(observed, fitted):
    """Centered coefficient of determination; undefined for constant data."""
    observed, fitted = np.asarray(observed, dtype=float), np.asarray(fitted, dtype=float)
    if not observed.size or observed.shape != fitted.shape or not np.isfinite(observed).all() or not np.isfinite(fitted).all():
        return None
    scale = float(np.max(np.abs(observed)))
    if scale == 0:
        return None
    observed, fitted = observed / scale, fitted / scale
    total = float(np.sum((observed - np.mean(observed))**2))
    if total <= np.finfo(float).eps**2 * observed.size:
        return None
    return float(1 - np.sum((observed - fitted)**2) / total)


@dataclass
class FitResult:
    region: str
    x: np.ndarray
    raw: np.ndarray
    baseline: np.ndarray
    corrected: np.ndarray
    centers: np.ndarray
    initial_amplitudes: np.ndarray
    amplitudes: np.ndarray
    widths: np.ndarray
    fitted: np.ndarray
    success: bool
    notes: list[str]
    diagnostics: dict
    mixing: np.ndarray | None = None
    line_shapes: list[str] | None = None
    extra_widths: np.ndarray | None = None
    profile_phases: np.ndarray | None = None

    def components(self):
        mixing = np.ones(len(self.centers)) if self.mixing is None else self.mixing
        if self.line_shapes is not None:
            return model_components(self.x, self.centers, self.widths, self.amplitudes,
                                    mixing, self.line_shapes, self.extra_widths, self.profile_phases)
        return peak_profile(self.x[:, None], self.centers[None, :],
                            self.widths[None, :], self.amplitudes[None, :], mixing[None, :])

    def areas(self):
        mixing = np.ones(len(self.centers)) if self.mixing is None else self.mixing
        if self.line_shapes is not None:
            return model_areas(self.widths, self.amplitudes, mixing, self.line_shapes,
                               self.extra_widths, self.profile_phases)
        return peak_area(self.amplitudes, self.widths, mixing)


def cancellation_diagnostics(components, centers, amplitudes, limit=.7):
    """Flag opposite-sign overlapping components, with pair IDs and scores."""
    pairs = []
    for i in range(len(amplitudes)):
        for j in range(i+1, len(amplitudes)):
            if amplitudes[i] * amplitudes[j] >= 0:
                continue
            a, b = components[:, i], components[:, j]
            denom = float(np.sum(np.abs(a) + np.abs(b)))
            if denom == 0:
                continue
            cancelled = 1 - float(np.sum(np.abs(a+b))) / denom
            if cancelled >= limit:
                pairs.append(dict(peak1=i+1, peak2=j+1, ppm1=float(centers[i]),
                                  ppm2=float(centers[j]), cancellation=cancelled))
    return pairs


def fit_region(spectrum: Spectrum, region: FitRegion, settings: Session) -> FitResult:
    xall = spectrum.ppm
    if region.low < xall[0] or region.high > xall[-1]:
        raise ValueError(f'{region.name}: границы области выходят за ppm-диапазон спектра.')
    mask = (xall >= region.low) & (xall <= region.high)
    x, y = xall[mask], spectrum.intensity[mask]
    corrected, line, notes = baseline_correct(x, y, region.baseline)
    f = settings.fit
    centers, picked = pick_peaks(x, corrected, f)
    n = len(centers)
    if n > f.max_peaks:
        raise ValueError(f'{region.name}: найдено {n} пиков (лимит {f.max_peaks}). '
                         'Повысьте threshold/разделение или сузьте область.')
    if not n:
        return FitResult(region.name, x, y, line, corrected, centers, picked,
                         np.array([]), np.array([]), np.zeros_like(y), True,
                         notes + ['Пики выше threshold не найдены; интеграл = 0.'],
                         {'n_peaks': 0, 'nfev': 0, 'relative_rmse': None,
                          'r_squared': r_squared(corrected, np.zeros_like(y))})
    if f.component_shapes or f.line_shape not in ('lorentzian', 'pseudo_voigt', 'gaussian'):
        from .flexible_fit import fit_profiles
        result = fit_profiles(x, corrected, centers, picked, spectrum, settings)
        result.region, result.raw, result.baseline = region.name, y, line
        result.notes = notes + result.notes
        return result
    nparams = n * (2 + int(f.refine_centers) + int(f.line_shape == 'pseudo_voigt'))
    if len(x) <= nparams:
        raise ValueError(f'{region.name}: недостаточно точек для {nparams} параметров.')
    scale = max(float(np.max(np.abs(corrected))), np.finfo(float).tiny)
    target, initial_amp = corrected / scale, picked / scale
    spacing = float(np.median(np.diff(x)))
    # A width appreciably below digitization resolution is unidentifiable.
    lower_w = max(f.min_width_hz / (2*spectrum.sf_mhz), spacing * .25)
    upper_w = f.max_width_hz / (2*spectrum.sf_mhz)
    if upper_w <= lower_w:
        raise ValueError('Максимальная ширина меньше цифрового разрешения спектра.')
    initial_w = max(settings.processing.lb_hz, f.initial_width_hz) / (2*spectrum.sf_mhz)
    initial_w = np.clip(initial_w, lower_w*1.01, upper_w*.99)
    initial_centers = centers.copy()
    center_scale = f.max_center_shift_hz / spectrum.sf_mhz
    center_low = np.maximum(centers - center_scale, x[0])
    center_high = np.minimum(centers + center_scale, x[-1])
    if n > 1:
        midpoints = (centers[:-1] + centers[1:]) / 2
        center_low[1:] = np.maximum(center_low[1:], midpoints + spacing * .01)
        center_high[:-1] = np.minimum(center_high[:-1], midpoints - spacing * .01)
    def unpack(params):
        amp, widths = params[:n], np.exp(params[n:2*n])
        position = 2*n
        fitted_centers = initial_centers
        if f.refine_centers:
            fitted_centers = initial_centers + params[position:position+n] * center_scale
            position += n
        mixing = params[position:position+n] if f.line_shape == 'pseudo_voigt' else np.full(n, 0. if f.line_shape == 'gaussian' else 1.)
        return amp, widths, fitted_centers, mixing
    def model_jac(params, jac=False):
        amp, widths, fitted_centers, mixing = unpack(params)
        difference = x[:, None] - fitted_centers[None, :]
        distances = difference**2
        w2 = widths**2
        lorentz = w2 / (distances + w2)
        gauss = np.exp(-np.log(2) * distances / w2)
        profile = mixing * lorentz + (1 - mixing) * gauss
        if jac:
            dlogw = amp * (mixing * 2*w2 * distances / (distances + w2)**2 +
                          (1 - mixing) * 2*np.log(2) * distances / w2 * gauss)
            blocks = [profile, dlogw]
            if f.refine_centers:
                blocks.append(amp * center_scale * (mixing * 2*w2 * difference / (distances + w2)**2 +
                              (1 - mixing) * 2*np.log(2) * difference / w2 * gauss))
            if f.line_shape == 'pseudo_voigt':
                blocks.append(amp * (lorentz - gauss))
            return np.concatenate(blocks, axis=1)
        return profile @ amp
    lower_amp = np.full(n, -np.inf if f.signed else 0.)
    lower = np.r_[lower_amp, np.full(n, np.log(lower_w))]
    upper = np.r_[np.full(n, np.inf), np.full(n, np.log(upper_w))]
    if f.refine_centers:
        lower = np.r_[lower, (center_low - centers) / center_scale]
        upper = np.r_[upper, (center_high - centers) / center_scale]
    if f.line_shape == 'pseudo_voigt':
        lower, upper = np.r_[lower, np.zeros(n)], np.r_[upper, np.ones(n)]
    candidates = []
    failures = []
    for attempt in range(f.restarts):
        factor = (1., .5, 2., .25, 4.)[attempt % 5]
        width0 = np.clip(initial_w*factor, lower_w*1.001, upper_w*.999)
        start = np.r_[initial_amp, np.full(n, np.log(width0))]
        if f.refine_centers:
            start = np.r_[start, np.zeros(n)]
        if f.line_shape == 'pseudo_voigt':
            start = np.r_[start, np.full(n, .5)]
        try:
            result = least_squares(lambda z: model_jac(z)-target, start,
                                   jac=lambda z: model_jac(z, True), bounds=(lower, upper),
                                   method='trf', x_scale='jac', max_nfev=f.max_nfev,
                                   tr_solver='lsmr' if n >= 20 else 'exact',
                                   loss=f.loss, f_scale=.03, ftol=1e-8, xtol=1e-8, gtol=1e-8)
            candidates.append(result)
        except (ValueError, FloatingPointError, np.linalg.LinAlgError) as exc:
            failures.append(str(exc))
    if not candidates:
        raise ValueError('Все попытки подгонки завершились ошибкой: ' + '; '.join(failures))
    good = [r for r in candidates if r.success and np.isfinite(r.x).all()]
    result = min(good or candidates, key=lambda r: r.cost)
    amplitudes, widths, centers, mixing = unpack(result.x)
    amplitudes = amplitudes * scale
    fitted = model_jac(result.x) * scale
    components = peak_profile(x[:, None], centers[None, :], widths[None, :], amplitudes[None, :], mixing[None, :])
    cancellation = cancellation_diagnostics(components, centers, amplitudes, f.cancellation_warning)
    rmse = float(np.sqrt(np.mean((target - fitted/scale)**2)))
    absolute_sum = np.sum(np.abs(components), axis=1)
    total_cancel = 1 - float(np.sum(np.abs(fitted))) / max(float(np.sum(absolute_sum)), np.finfo(float).tiny)
    jac = model_jac(result.x, True)
    # Normalize columns so condition reflects overlap, not amplitude units.
    norms = np.linalg.norm(jac, axis=0)
    cond = float(np.linalg.cond(jac / np.maximum(norms, np.finfo(float).tiny)))
    diagnostics = dict(n_peaks=n, nfev=int(result.nfev), attempts=len(candidates),
                       solver_message=str(result.message), relative_rmse=rmse,
                       condition_number=cond if np.isfinite(cond) else None, cancellation_fraction=total_cancel,
                       cancellation_pairs=cancellation,
                       initial_width_ppm=float(initial_w), width_bounds_ppm=[lower_w, upper_w])
    diagnostics.update(line_shape=f.line_shape, refined_centers=f.refine_centers,
                       center_shifts_hz=((centers - initial_centers) * spectrum.sf_mhz).tolist(),
                       r_squared=r_squared(corrected, fitted))
    if not result.success:
        notes.append('ОПТИМИЗАЦИЯ НЕ СОШЛАСЬ: ' + str(result.message))
    if rmse > f.residual_warning:
        notes.append(f'Большой остаток: относительный RMSE={rmse:.3g}.')
    if cond > 1e6:
        notes.append(f'Параметры плохо различимы: condition={cond:.3g}.')
    if np.any((widths < lower_w*1.02) | (widths > upper_w*.98)):
        notes.append('Одна или несколько ширин достигли границы ограничений.')
    if f.refine_centers and np.any((centers <= center_low + spacing*.05) | (centers >= center_high - spacing*.05)):
        notes.append('Один или несколько центров достигли границы уточнения.')
    if np.max(np.abs(amplitudes)) > scale*5:
        notes.append('Амплитуда компоненты превышает максимум данных более чем в 5 раз.')
    if cancellation:
        notes.append('Взаимная компенсация противоположных линий: ' +
                     ', '.join(f"{r['peak1']} / {r['peak2']} ({r['cancellation']:.0%})" for r in cancellation))
    if total_cancel >= f.cancellation_warning:
        notes.append(f'Суммарная компенсация линий {total_cancel:.0%}.')
    return FitResult(region.name, x, y, line, corrected, centers, picked, amplitudes,
                     widths, fitted, bool(result.success), notes, diagnostics, mixing.copy())


@dataclass
class Analysis:
    spectrum: Spectrum
    results: dict[str, FitResult]
    integrals: dict[str, float]
    errors: dict[str, str]
    status: str
    notes: list[str]


def analyze(spectrum: Spectrum, session: Session) -> Analysis:
    session.validate(require_regions=True)
    results, errors = {}, {}
    notes = list(spectrum.notes)
    for region in session.regions:
        try:
            result = fit_region(spectrum, region, session)
            results[region.name] = result
            notes.extend(f'{region.name}: {n}' for n in result.notes)
            if not result.success:
                errors[region.name] = result.diagnostics['solver_message']
        except Exception as exc:
            errors[region.name] = str(exc)
            notes.append(f'{region.name}: ОШИБКА: {exc}')
    integrals = {}
    for area in session.integrals:
        result = results.get(area.parent)
        if result is None or area.parent in errors:
            integrals[area.name] = float('nan')
        else:
            keep = (result.centers >= area.low) & (result.centers <= area.high)
            integrals[area.name] = float(np.sum(result.areas()[keep]) / np.pi)
    return Analysis(spectrum, results, integrals, errors,
                    'error' if errors else 'warning' if notes else 'ok', notes)


def discover_spectra(root, recursive=True):
    root = Path(root)
    if not root.is_dir():
        raise ValueError('Выберите существующую папку серии.')
    found = set()
    pattern = '**/fid' if recursive else '*/fid'
    for fid in root.glob(pattern):
        if (fid.parent / 'acqus').is_file():
            found.add(fid.parent)
    if (root / 'fid').is_file() and (root / 'acqus').is_file():
        found.add(root)
    found.update(root.glob('**/*.npz' if recursive else '*.npz'))
    def key(path):
        # Natural sorting, preserving unique relative paths as spectrum IDs.
        import re
        return [(0, int(s)) if s.isdigit() else (1, s.casefold())
                for s in re.split(r'(\d+)', path.relative_to(root).as_posix())]
    return sorted(found, key=key)


def write_batch_csv(path, rows, session: Session):
    """UTF-8 BOM for Excel. Failed values are NaN, never fabricated zeroes."""
    fields = ['sequence_index', 'spectrum_number', 'spectrum_id', 'source_path']
    fields += [f'integral__{r.name}' for r in session.integrals]
    fields += ['status', 'warnings', 'phase_p0_deg', 'phase_p1_deg', 'peak_count', 'max_relative_rmse']
    with Path(path).open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def batch_analyze(root, session: Session, csv_path, progress: Callable | None = None,
                  cancelled: Callable | None = None, recursive=True):
    session.validate(require_regions=True)
    paths = discover_spectra(root, recursive)
    if not paths:
        raise ValueError('Не найдены папки с fid + acqus или демонстрационные .npz.')
    root = Path(root)
    csv_path = Path(csv_path)
    # Save the exact recipe alongside the table for reproducibility.
    session.save(csv_path.with_suffix('.settings.json'))
    rows, report = [], []
    was_cancelled = False
    for number, path in enumerate(paths, 1):
        if cancelled and cancelled():
            was_cancelled = True
            break
        ident = path.relative_to(root).as_posix()
        row = dict(sequence_index=number, spectrum_number=path.stem if path.is_file() else path.name,
                   spectrum_id=ident, source_path=str(path),
                   status='error', warnings='', phase_p0_deg='', phase_p1_deg='',
                   peak_count='', max_relative_rmse='')
        row.update({f'integral__{r.name}': float('nan') for r in session.integrals})
        try:
            raw = load_raw(path, session.processing.remove_digital_filter)
            analysis = analyze(process(raw, session.processing), session)
            row.update({f'integral__{n}': v for n, v in analysis.integrals.items()})
            row.update(status=analysis.status, warnings=' | '.join(analysis.notes),
                       phase_p0_deg=analysis.spectrum.phases[0], phase_p1_deg=analysis.spectrum.phases[1],
                       peak_count=sum(len(r.centers) for r in analysis.results.values()))
            rmses = [r.diagnostics.get('relative_rmse') for r in analysis.results.values()]
            row['max_relative_rmse'] = max((v for v in rmses if v is not None), default='')
            report.append(dict(spectrum_id=ident, errors=analysis.errors,
                               diagnostics={n: r.diagnostics for n, r in analysis.results.items()}))
        except Exception as exc:
            row['warnings'] = str(exc)
            report.append(dict(spectrum_id=ident, errors={'spectrum': str(exc)}))
        rows.append(row)
        if progress:
            progress(number, len(paths), row)
    write_batch_csv(csv_path, rows, session)
    csv_path.with_suffix('.diagnostics.json').write_text(
        json.dumps(dict(cancelled=was_cancelled, processed=len(rows), total=len(paths), spectra=report),
                   ensure_ascii=False, indent=2), encoding='utf-8')
    return rows, was_cancelled
