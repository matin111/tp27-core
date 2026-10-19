from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_server_speed_retries_transient_iperf_with_safe_profile():
    text = (ROOT / 'panel/static/server-speed-ui.js').read_text(encoding='utf-8')
    assert 'SAFE_PARALLEL=1' in text
    assert 'transientIperf' in text
    assert 'benchmarkTunnel(x,{duration:SAFE_DURATION,parallel:SAFE_PARALLEL})' in text


def test_server_speed_never_dumps_raw_iperf_json_to_alert():
    text = (ROOT / 'panel/static/server-speed-ui.js').read_text(encoding='utf-8')
    assert 'friendlyBenchmarkError' in text
    assert 'اندازه‌گیری iperf3 کامل نشد' in text
    assert "alert('تست سرعت ناموفق بود: '+friendlyBenchmarkError(e))" in text
