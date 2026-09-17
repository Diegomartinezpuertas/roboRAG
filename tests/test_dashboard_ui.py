"""Browser tests for the dashboard page: real HTML/JS in headless Chromium (ADR-030).

The HTTP layer has been tested for months; the page on top of it had not — and
"the robot doesn't move" was reported from exactly that gap, where a key press
has to travel through JavaScript before it becomes an HTTP request. These tests
serve the real app (web_api.build_app over the stub node from test_web_api) and
drive the page as a person would: press W, type an order, delete a memory.

Skipped when Playwright or its Chromium is not installed:
    pip install playwright && python3 -m playwright install chromium
"""

import socket
import threading
import time

import pytest

playwright_api = pytest.importorskip('playwright.sync_api')

import uvicorn  # noqa: E402
from test_web_api import MEMORY_ITEMS, FakeNode  # noqa: E402

from robot_dashboard.web_api import build_app  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.fixture(scope='module')
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception as exc:  # noqa: BLE001 - no browser installed on this machine
            pytest.skip(f'Chromium unavailable: {exc}')
        yield chromium
        chromium.close()


@pytest.fixture
def node():
    return FakeNode(pose={'x': 0.5, 'y': -1.0, 'yaw': 0.0})


@pytest.fixture
def page(browser, node):
    port = _free_port()
    config = uvicorn.Config(build_app(node), host='127.0.0.1', port=port, log_level='error')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    tab = browser.new_page(viewport={'width': 1440, 'height': 900})
    tab.errors = []
    tab.on('pageerror', lambda e: tab.errors.append(str(e)))
    tab.on('console', lambda m: m.type == 'error' and tab.errors.append(m.text))
    tab.goto(f'http://127.0.0.1:{port}/')
    tab.wait_for_selector('#conn.ok', timeout=10000)
    yield tab
    tab.close()
    server.should_exit = True
    thread.join(timeout=5)


def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_the_page_loads_without_script_errors(page):
    assert page.locator('canvas#mapCanvas').count() == 1
    page.wait_for_timeout(500)
    assert page.errors == []


def test_holding_w_while_driving_sends_the_key_and_letting_go_stops(page, node):
    """The path of the bug report: key press -> JavaScript -> /api/teleop."""
    page.click('#teleBtn')
    page.click('#mapCanvas', position={'x': 400, 'y': 300})      # focus the plan, as a person would
    page.keyboard.down('w')
    assert _until(lambda: (['w'], False) in node.drive_calls), node.drive_calls
    assert page.locator('.key.w.down').count() == 1
    page.keyboard.up('w')
    assert _until(lambda: node.stop_calls >= 1)


def test_keys_do_nothing_until_driving_is_turned_on(page, node):
    page.click('#mapCanvas', position={'x': 400, 'y': 300})
    page.keyboard.down('w')
    page.wait_for_timeout(400)
    page.keyboard.up('w')
    assert node.drive_calls == []


def test_typing_an_order_never_drives_the_robot(page, node):
    page.click('#teleBtn')
    page.fill('#goalInput', '')
    page.click('#goalInput')
    page.keyboard.type('ve a la sala de estar')              # full of a, s, d, w
    page.keyboard.press('Enter')
    assert _until(lambda: node.published_goals == ['ve a la sala de estar'])
    assert not any('w' in keys or 'a' in keys for keys, _ in node.drive_calls)


def test_the_drive_panel_says_who_holds_the_robot(page, node):
    node.sim_status = {'rtf': 0.2, 'driver': 'teleop'}
    assert _until(lambda: page.inner_text('#driverState') == 'Conduces tú')
    assert _until(lambda: page.inner_text('#simRtf') == '×0.20')
    node.sim_status = {'rtf': None, 'driver': None}
    assert _until(lambda: 'cmd_vel_mux_node' in page.inner_text('#driverState'))


def test_the_agent_thread_shows_order_reasoning_steps_and_answer(page, node):
    now = time.time()

    def event(event_id, kind, text):
        return {'id': event_id, 'ts': now, 'type': kind, 'text': text, 'source': '', 'level': 20}

    node.events._events.extend([
        event(1, 'goal', 'Ve donde se suele cocinar'),
        event(2, 'status', 'Qwen razona: the kitchen is where meals are cooked'),
        event(3, 'status', 'Plan paso 1/1: navigate({"x": 3.8, "y": -2.6})'),
        event(4, 'response', 'He llegado a la cocina.'),
    ])
    page.wait_for_selector('.t-answer', timeout=5000)
    assert page.inner_text('.t-goal').endswith('Ve donde se suele cocinar')
    assert 'meals are cooked' in page.inner_text('.t-think')
    assert 'navigate' in page.inner_text('.t-step')


def _show_memory(page, node):
    node.memory_result = {'ok': True, 'items': MEMORY_ITEMS, 'map_id': 'abc123',
                          'stats': {'semantic_map': 3}, 'error': ''}
    page.click('#memTabs .tab >> nth=0')
    page.wait_for_selector('.note h3', timeout=5000)


def test_memory_cards_say_what_kind_of_memory_they_are(page, node):
    _show_memory(page, node)
    first = page.locator('.note').first
    assert first.locator('h3').inner_text().startswith('cocina')
    assert 'habitación nombrada' in first.locator('h3').inner_text()
    # The card already shows the place; the text does not repeat "kitchen at (x=…)".
    assert not first.locator('.doc').inner_text().startswith('kitchen at')


def test_deleting_a_memory_card_deletes_the_ids_it_stands_for(page, node):
    _show_memory(page, node)
    page.once('dialog', lambda dialog: dialog.accept())
    page.locator('.note').first.locator('button.del').click()
    expected = [('semantic_map', ['zone-cocina'])]
    assert _until(lambda: node.delete_calls == expected), node.delete_calls


def test_the_clear_button_empties_the_thread_and_the_server_buffer(page, node):
    now = time.time()
    node.events._events.append(
        {'id': 1, 'ts': now, 'type': 'goal', 'text': 'Ve a la cocina', 'source': '', 'level': 20})
    page.wait_for_selector('.t-goal', timeout=5000)
    page.click('#clearThreadBtn')
    page.wait_for_selector('#threadEmpty', timeout=5000)
    assert page.locator('.t-goal').count() == 0
    assert node.events._events == []


def test_the_trail_button_clears_the_robots_track_and_stops_drawing_it(page, node):
    """Recording wants a clean floor plan; the yellow trail is switched off here."""
    canvas = page.locator('#mapCanvas')
    for x in (0.5, 1.0, 1.5, 2.0):          # the poll turns pose changes into trail points
        node._pose = {'x': x, 'y': -1.0, 'yaw': 0.0}
        page.wait_for_timeout(350)
    assert int(canvas.get_attribute('data-trail')) > 1
    page.click('#trailBtn')
    assert page.get_attribute('#trailBtn', 'aria-pressed') == 'false'
    assert canvas.get_attribute('data-trail') == '0'
    node._pose = {'x': 3.0, 'y': -1.0, 'yaw': 0.0}
    page.wait_for_timeout(600)
    assert canvas.get_attribute('data-trail') == '0'      # and it stays off
    page.click('#trailBtn')
    assert page.get_attribute('#trailBtn', 'aria-pressed') == 'true'
