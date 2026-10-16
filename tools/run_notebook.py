"""Chạy notebook từ đầu đến cuối và ghi output vào chính file .ipynb trong lúc chạy.

Dùng cho task VS Code: mở notebook trong editor, chạy task, editor tự tải lại khi file đổi
nên thấy từng cell ra kết quả. Không chỉnh notebook khi đang chạy (VS Code sẽ hỏi ghi đè).

    python tools/run_notebook.py data/02_standardise.ipynb [model/10_lightgbm_tc.ipynb ...]
    python tools/run_notebook.py --allow-errors model/11_hybrid_tc.ipynb

Thư mục làm việc của kernel là thư mục chứa notebook (giống khi chạy trong VS Code),
nên đường dẫn tương đối trong notebook giữ nguyên.
"""
import argparse, asyncio, os, sys, time, warnings
import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

SAVE_EVERY = 3.0   # giây giữa hai lần ghi file khi một cell đang in output


def fmt(sec):
    sec = int(sec)
    return f"{sec // 3600:d}h{(sec % 3600) // 60:02d}m{sec % 60:02d}s" if sec >= 3600 else f"{sec // 60:d}m{sec % 60:02d}s"


def short(path):
    try:
        return os.path.relpath(path)
    except ValueError:   # khác ổ đĩa trên Windows
        return path


class LiveSavingClient(NotebookClient):
    """NotebookClient ghi file sau mỗi cell và định kỳ trong lúc cell đang chạy."""

    def __init__(self, nb, path, **kw):
        super().__init__(nb, **kw)
        self.path, self._last_save = path, 0.0
        self.code_cells = [i for i, c in enumerate(nb.cells) if c.cell_type == "code"]
        self.on_cell_start = self._on_start
        self.on_cell_executed = self._on_done

    def save(self):
        tmp = f"{self.path}.tmp"
        nbformat.write(self.nb, tmp)
        os.replace(tmp, self.path)   # ghi xong mới thay, editor không đọc phải file dở
        self._last_save = time.time()

    def output(self, outs, msg, display_id, cell_index):
        out = super().output(outs, msg, display_id, cell_index)
        if self._first_output or time.time() - self._last_save >= SAVE_EVERY:
            self._first_output = False
            self.save()
        return out

    def _on_start(self, cell, cell_index):
        self._t, self._first_output = time.time(), True
        if cell_index not in self.code_cells:
            return
        k = self.code_cells.index(cell_index) + 1
        head = next((l for l in cell.source.splitlines() if l.strip()), "").strip()[:70]
        print(f"▶ [{k}/{len(self.code_cells)}] cell {cell_index}: {head}", flush=True)

    def _on_done(self, cell, cell_index, execute_reply):
        if cell_index not in self.code_cells:
            return
        ok = execute_reply.get("content", {}).get("status") == "ok"
        print(f"  {'✓' if ok else '✗'} {fmt(time.time() - self._t)}", flush=True)
        self.save()


def run(path, allow_errors, timeout):
    path = os.path.abspath(path)
    nb = nbformat.read(path, as_version=4)
    for c in nb.cells:   # xóa output cũ để dễ thấy cell nào đã chạy lại
        if c.cell_type == "code":
            c.outputs, c.execution_count = [], None
    client = LiveSavingClient(
        nb, path, timeout=timeout, allow_errors=allow_errors,
        kernel_name=nb.metadata.get("kernelspec", {}).get("name", "python3"),
        resources={"metadata": {"path": os.path.dirname(path)}},
    )
    client.save()
    print(f"=== {short(path)} ({len(client.code_cells)} cell code)", flush=True)
    t0 = time.time()
    try:
        client.execute()
    except CellExecutionError as e:
        client.save()
        print(f"\n✗ Dừng vì lỗi sau {fmt(time.time() - t0)}. Lỗi đã ghi trong notebook.\n{str(e)[-1500:]}", flush=True)
        return False
    except KeyboardInterrupt:
        client.save()
        print("\n■ Đã dừng thủ công. Các cell đã chạy được giữ trong notebook.", flush=True)
        return False
    print(f"=== Xong {short(path)} sau {fmt(time.time() - t0)}\n", flush=True)
    return True


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.platform == "win32":   # zmq cần selector loop, tránh cảnh báo Proactor trên Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    warnings.filterwarnings("ignore", category=RuntimeWarning, module="zmq")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("notebooks", nargs="+", help="chạy lần lượt; dừng ở notebook đầu tiên bị lỗi")
    ap.add_argument("--allow-errors", action="store_true", help="cell lỗi vẫn chạy tiếp các cell sau")
    ap.add_argument("--timeout", type=int, default=None, help="giới hạn giây mỗi cell (mặc định: không giới hạn)")
    a = ap.parse_args()
    for nb in a.notebooks:
        if not run(nb, a.allow_errors, a.timeout):
            sys.exit(1)


if __name__ == "__main__":
    main()
