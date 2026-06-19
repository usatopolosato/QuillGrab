from app import create_app
import webbrowser
import threading

app = create_app()

if __name__ == '__main__':
    # Открываем браузер через 1.5 сек
    threading.Timer(1.5, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(host='127.0.0.1', port=5000, debug=False)