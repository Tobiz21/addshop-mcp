"""Local interactive login. Password/cookies stay in this process's memory."""
import importlib
import json
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from addshop import Addshop
from login_cli import owner_login

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work' / 'addshop-live'
WORK.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(WORK))
client = Addshop('https://new15676011817139.myaddshop.ru', state_dir=WORK / 'journals')


def save(name, data):
    target = WORK / name
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)


window = tk.Tk()
window.title('Addshop — локальный вход для MCP')
window.geometry('550x290')
window.attributes('-topmost', True)
frame = ttk.Frame(window, padding=20)
frame.pack(fill='both', expand=True)
ttk.Label(frame, text='Вход владельца Addshop', font=('Segoe UI', 14)).pack(anchor='w')
ttk.Label(frame, text='Данные отправляются только на https://addshop.ru/login/\nПароль не сохраняется в файлы и не передаётся в чат.').pack(anchor='w', pady=(8,12))
ttk.Label(frame, text='Логин / email').pack(anchor='w')
email = ttk.Entry(frame, width=60)
email.pack(fill='x')
ttk.Label(frame, text='Пароль').pack(anchor='w', pady=(8,0))
password = ttk.Entry(frame, show='●', width=60)
password.pack(fill='x')
status = ttk.Label(frame, text='После входа оставьте окно открытым до завершения проверки.')
status.pack(anchor='w', pady=8)
ready = False
busy = False


def login():
    global busy
    if busy:
        return
    busy = True
    username, secret = email.get(), password.get()
    password.delete(0, 'end')
    button.configure(state='disabled')
    status.configure(text='Выполняется вход…')
    def run():
        global ready, busy
        try:
            owner_login(client, username, secret)
            card = client.read(296869)
            save('status.json', {'status': 'authenticated', 'read': card.public()})
            ready = True
            window.after(0, lambda: status.configure(text='Вход выполнен. Проверку продолжает ассистент.'))
            window.after(0, lambda: window.attributes('-topmost', False))
        except Exception as exc:
            save('status.json', {'status': 'login_failed', 'error_type': type(exc).__name__})
            window.after(0, lambda: status.configure(text='Вход не подтверждён. Ассистент проверит причину.'))
            window.after(0, lambda: button.configure(state='normal'))
        finally:
            busy = False
    threading.Thread(target=run, daemon=True).start()


button = ttk.Button(frame, text='Войти и разрешить проверку', command=login)
button.pack(anchor='w')
window.bind('<Return>', lambda event: login())
email.focus_set()
save('status.json', {'status': 'awaiting_login'})


def poll():
    global busy
    command = WORK / 'command.json'
    if ready and not busy and command.exists():
        busy = True
        request = json.loads(command.read_text('utf-8'))
        command.unlink()
        def task():
            global busy
            try:
                import live_tasks
                importlib.reload(live_tasks)
                result = live_tasks.handle(client, request)
                save('result.json', {'id': request['id'], 'ok': True, 'result': result})
            except Exception as exc:
                save('result.json', {'id': request['id'], 'ok': False, 'error_type': type(exc).__name__, 'error': str(exc) if type(exc).__name__ == 'AddshopError' else 'Operation failed; no automatic retry.'})
            finally:
                busy = False
        threading.Thread(target=task, daemon=True).start()
    window.after(500, poll)


window.after(500, poll)
window.mainloop()
client.http.close()
