import copy
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from spintegrate.core import (BASELINE_METHODS, BaselineSettings, FitRegion,
    FitSettings, IntegrationRegion, Session, Spectrum, analyze, baseline_correct,
    batch_analyze, cancellation_diagnostics, fit_region, load_raw, lorentzian,
    pick_peaks, process)
from spintegrate.demo import demo_raw, demo_session, write_demo_series


def numerical_spectrum(x, y, sf=400.):
    return Spectrum(x, y, y.astype(complex), sf, sf*(x[-1]-x[0]),
                    'synthetic', (0., 0.), [])


def test_fixed_centers_heights_widths_and_exact_requested_integral():
    x = np.linspace(3., 4., 4001)
    y = lorentzian(x, 3.5, .006, 12000.) + lorentzian(x, 3.58, .009, 6500.)
    spectrum = numerical_spectrum(x, y)
    s = Session()
    s.fit.threshold = 1500.
    s.regions = [FitRegion('fit', 3., 4., BaselineSettings(method='none'))]
    s.integrals = [IntegrationRegion('first', 'fit', 3.48, 3.52),
                   IntegrationRegion('both', 'fit', 3.4, 3.7)]
    picked_centers, picked_heights = pick_peaks(x, y, s.fit)
    analysis = analyze(spectrum, s)
    result = analysis.results['fit']
    assert result.success
    np.testing.assert_array_equal(result.centers, picked_centers)
    np.testing.assert_array_equal(result.initial_amplitudes, picked_heights)
    np.testing.assert_allclose(result.widths, [.006, .009], rtol=.01)
    np.testing.assert_allclose(result.amplitudes, [12000., 6500.], rtol=.01)
    assert analysis.integrals['first'] == pytest.approx(result.amplitudes[0]*result.widths[0])
    assert analysis.integrals['both'] == pytest.approx(np.sum(result.amplitudes*result.widths))
    assert analysis.integrals['both'] == pytest.approx(130.5, rel=.01)
    assert np.all(result.amplitudes >= 0)


def test_region_validation_and_recipe_roundtrip(tmp_path):
    s = demo_session()
    path = tmp_path / 'settings.json'
    s.save(path)
    assert Session.load(path) == s
    s.integrals[0].low = 1.
    with pytest.raises(ValueError, match='внутри'):
        s.validate()
    s = demo_session()
    s.regions[1].low = 3.7
    s.validate()  # overlapping contexts are allowed; each integral has one parent
    s.regions[1].name = s.regions[0].name
    with pytest.raises(ValueError, match='уникальны'):
        s.validate()


@pytest.mark.parametrize('method', BASELINE_METHODS)
def test_baseline_methods_on_ascending_local_region(method):
    x = np.linspace(3., 4., 501)
    line = 100 + 40*(x-3)
    y = line + lorentzian(x, 3.5, .008, 2000.)
    corrected, baseline, notes = baseline_correct(x, y, BaselineSettings(method=method))
    np.testing.assert_allclose(corrected+baseline, y)
    assert np.isfinite(baseline).all()
    assert baseline.shape == x.shape
    if method == 'none':
        np.testing.assert_array_equal(corrected, y)


def test_abs_fid_and_abs_spectrum_are_distinct_and_do_not_mutate_fid():
    raw = demo_raw()
    original = raw.fid.copy()
    s = demo_session()
    s.processing.phase = 'abs'
    s.processing.abs_source = 'spectrum'
    magnitude = process(raw, s.processing)
    s.processing.abs_source = 'fid'
    fid_abs = process(raw, s.processing)
    assert np.min(magnitude.intensity) >= 0
    assert not np.allclose(magnitude.intensity, fid_abs.intensity)
    np.testing.assert_array_equal(raw.fid, original)
    assert np.all(np.diff(magnitude.ppm) > 0)


def test_ppm_metadata_fft_order_reference_and_apodization_hz():
    raw = demo_raw()
    p = demo_session().processing
    p.lb_hz = 0
    spectrum = process(raw, p)
    strongest = spectrum.ppm[np.argmax(spectrum.intensity)]
    assert strongest == pytest.approx(7.15, abs=raw.sw_hz/raw.sf_mhz/raw.fid.size)
    p.reference_mode = 'strongest'
    p.reference_ppm = 7.35
    referenced = process(raw, p)
    assert referenced.ppm[np.argmax(np.abs(referenced.complex_spectrum))] == pytest.approx(7.35)
    p.reference_mode = 'metadata'
    p.lb_hz = 4.
    apodized = process(raw, p)
    assert np.max(apodized.intensity) < np.max(spectrum.intensity)
    # Fit an isolated analytic peak: exponential LB adds FWHM=LB/sf in ppm.
    s = demo_session()
    s.processing = p
    s.regions = [FitRegion('reference', 6.8, 7.5, BaselineSettings(method='asls', p=.001, lam=1e8))]
    result = fit_region(apodized, s.regions[0], s)
    assert len(result.centers) == 1
    assert result.widths[0] == pytest.approx(.004 + 4/(2*raw.sf_mhz), rel=.12)


def test_fft_bin_direction_against_analytic_time_domain_fid():
    from spintegrate.core import RawSpectrum
    sf, sw, carrier, size = 400., 4800., 6., 8192
    frequency = 600.  # exactly on a sampled FFT bin, +1.5 ppm from carrier
    t = np.arange(size)/sw
    fid = np.exp(-np.pi*4*t) * np.exp(2j*np.pi*frequency*t)
    raw = RawSpectrum(fid, sf, sw, carrier, 'analytic FID')
    spectrum = process(raw, Session().processing)
    assert spectrum.ppm[np.argmax(spectrum.intensity)] == pytest.approx(7.5)


@pytest.mark.parametrize('mode', ['none', 'acme', 'peak_minima', 'abs'])
def test_all_phase_modes_produce_finite_real_spectra(mode):
    s = demo_session()
    s.processing.phase = mode
    result = process(demo_raw(), s.processing)
    assert np.isrealobj(result.intensity)
    assert np.isfinite(result.intensity).all()


def test_opposite_large_overlapping_lorentzians_are_detected():
    x = np.linspace(3., 4., 1001)
    centers = np.array([3.5, 3.501])
    amplitudes = np.array([1e6, -9.9e5])
    components = np.column_stack([lorentzian(x, c, .02, a) for c, a in zip(centers, amplitudes)])
    pairs = cancellation_diagnostics(components, centers, amplitudes)
    assert len(pairs) == 1 and pairs[0]['cancellation'] > .9
    assert not cancellation_diagnostics(np.abs(components), centers, np.abs(amplitudes))


def test_signed_negative_peaks_and_cancellation_diagnostics_available():
    x = np.linspace(3., 4., 2001)
    y = lorentzian(x, 3.4, .007, 10000.) + lorentzian(x, 3.65, .008, -7000.)
    s = Session()
    s.fit.threshold = 500.
    s.fit.signed = True
    r = FitRegion('signed', 3., 4., BaselineSettings(method='none'))
    result = fit_region(numerical_spectrum(x, y), r, s)
    assert result.success
    assert len(result.centers) == 2
    assert result.amplitudes[1] < 0
    np.testing.assert_allclose(result.widths, [.007, .008], rtol=.02)
    assert 'cancellation_pairs' in result.diagnostics


def test_nonconvergence_is_nan_not_plausible_integral():
    x = np.linspace(3., 4., 1001)
    y = lorentzian(x, 3.5, .01, 10000.)
    s = Session()
    s.fit.threshold = 100.
    s.fit.max_nfev = 1
    s.fit.restarts = 1
    s.regions = [FitRegion('fit', 3., 4., BaselineSettings(method='none'))]
    s.integrals = [IntegrationRegion('area', 'fit', 3.4, 3.6)]
    result = analyze(numerical_spectrum(x, y), s)
    assert result.status == 'error'
    assert not result.results['fit'].success
    assert np.isnan(result.integrals['area'])


def test_zero_peaks_and_out_of_range_region_are_explicit():
    x = np.linspace(3., 4., 101)
    spectrum = numerical_spectrum(x, np.zeros_like(x))
    s = Session(regions=[FitRegion('fit', 3., 4., BaselineSettings(method='none'))],
                integrals=[IntegrationRegion('area', 'fit', 3.4, 3.6)])
    result = analyze(spectrum, s)
    assert result.integrals['area'] == 0
    assert result.status == 'warning'
    s.regions[0].low = 2.
    result = analyze(spectrum, s)
    assert result.status == 'error'
    assert np.isnan(result.integrals['area'])


def test_many_parameters_converge_with_fixed_centers():
    x = np.linspace(0., 4., 4001)
    centers = np.linspace(.15, 3.85, 24)
    y = sum(lorentzian(x, c, .003 + i*.0001, 2000.+i*100) for i, c in enumerate(centers))
    s = Session()
    s.fit.threshold = 300.
    s.fit.restarts = 1
    r = FitRegion('many', 0., 4., BaselineSettings(method='none'))
    result = fit_region(numerical_spectrum(x, y), r, s)
    assert result.success
    assert len(result.centers) == 24
    assert result.diagnostics['relative_rmse'] < .02


def test_batch_order_scaling_errors_and_output_recipe(tmp_path):
    write_demo_series(tmp_path)
    # Corrupted spectrum must produce its own error row while others continue.
    (tmp_path / '10.npz').write_bytes(b'broken spectrum')
    s = demo_session()
    observed = []
    rows, stopped = batch_analyze(tmp_path, s, tmp_path / 'integrals.csv',
                                 progress=lambda n, total, row: observed.append(n))
    assert not stopped
    assert [r['spectrum_id'] for r in rows] == ['1.npz', '2.npz', '3.npz', '4.npz', '10.npz']
    assert observed == list(range(1, 6))
    assert rows[-1]['status'] == 'error'
    assert np.isnan(rows[-1]['integral__signal'])
    assert rows[3]['integral__signal']/rows[0]['integral__signal'] == pytest.approx(.4, rel=.04)
    assert Session.load(tmp_path/'integrals.settings.json') == s
    diagnostics = json.loads((tmp_path/'integrals.diagnostics.json').read_text(encoding='utf-8'))
    assert diagnostics['total'] == 5
    with (tmp_path/'integrals.csv').open(encoding='utf-8-sig') as stream:
        csvrows = list(csv.DictReader(stream))
    assert len(csvrows) == 5
    assert csvrows[-1]['integral__signal'] == 'nan'


def test_batch_cancel_exports_partial_rows(tmp_path):
    write_demo_series(tmp_path)
    done = []
    rows, stopped = batch_analyze(tmp_path, demo_session(), tmp_path/'partial.csv',
                progress=lambda *args: done.append(1), cancelled=lambda: bool(done))
    assert stopped
    assert len(rows) == 1
    assert (tmp_path/'partial.csv').exists()


def test_actual_bruker_directory_io_and_experiment_numbers(tmp_path):
    import nmrglue as ng
    sf, sw, carrier, size = 400., 4800., 6., 8192
    t = np.arange(size)/sw
    fid = 10000*np.exp(-np.pi*4*t)*np.exp(2j*np.pi*600*t)
    for expno, scale in [(10, .5), (2, 1.)]:
        u = ng.fileiobase.create_blank_udic(1)
        u[0].update(size=size, sw=sw, obs=sf, car=carrier*sf, complex=True)
        d = ng.bruker.create_dic(u)
        d['acqus'].update(SFO1=sf, SW_h=sw, O1=carrier*sf, GRPDLY=0., DECIM=1, DTYPA=2)
        directory = tmp_path/str(expno)
        directory.mkdir()
        ng.bruker.write(str(directory), d, fid*scale, isfloat=True, write_prog=False)
    raw = load_raw(tmp_path/'2')
    np.testing.assert_allclose(raw.fid, fid)
    assert raw.sf_mhz == sf and raw.sw_hz == sw
    s = Session()
    s.processing.lb_hz = 0.
    s.fit.threshold = 5000.
    s.fit.initial_width_hz = 4.
    s.regions = [FitRegion('fit', 7.2, 7.8, BaselineSettings(method='asls', lam=1e8, p=.001))]
    s.integrals = [IntegrationRegion('signal', 'fit', 7.4, 7.6)]
    rows, stopped = batch_analyze(tmp_path, s, tmp_path/'bruker.csv')
    assert [r['spectrum_number'] for r in rows] == ['2', '10']
    assert all(r['status'] != 'error' for r in rows)
    assert rows[1]['integral__signal']/rows[0]['integral__signal'] == pytest.approx(.5, rel=.01)


def test_baseline_processing_does_not_modify_global_spectrum():
    s = demo_session()
    spectrum = process(demo_raw(), s.processing)
    original = spectrum.intensity.copy()
    result = analyze(spectrum, s)
    assert result.results
    np.testing.assert_array_equal(spectrum.intensity, original)
