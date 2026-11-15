"""WinForms close callbacks must leave the UI thread available for JS results."""
import threading
from types import SimpleNamespace
from app.desktop import close_guard

def test_close_guard_releases_ui_before_inspecting_changes():
    ui_released=threading.Event();destroyed=threading.Event();calls=[]
    def evaluate(script):
        assert threading.current_thread() is not threading.main_thread()
        assert ui_released.wait(2)
        return False
    window=SimpleNamespace(evaluate_js=evaluate,destroy=destroyed.set)
    closing=close_guard(window,SimpleNamespace(state=SimpleNamespace()))
    assert closing() is False
    assert closing() is False
    assert not destroyed.is_set()
    ui_released.set()
    assert destroyed.wait(2)
    assert closing() is True

def test_close_guard_preserves_cancelled_edits_and_allows_retry():
    finished=threading.Event();destroyed=threading.Event();answers=iter([False,True])
    def confirm(*args):
        answer=next(answers);finished.set();return answer
    window=SimpleNamespace(evaluate_js=lambda script:True,create_confirmation_dialog=confirm,destroy=destroyed.set)
    closing=close_guard(window,SimpleNamespace(state=SimpleNamespace()))
    assert closing() is False
    assert finished.wait(2)
    # Wait for the worker's finally block without relying on a scheduling delay.
    for worker in threading.enumerate():
        if worker.name=='invoice-close-check':worker.join(2)
    assert not destroyed.is_set()
    assert closing() is False
    assert destroyed.wait(2)
    assert closing() is True
