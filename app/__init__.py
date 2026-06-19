from flask import Flask
from app.config import Config
from app.model_manager import ModelManager

def create_app():
    app = Flask(__name__, template_folder='../templates', static_folder='../static')
    app.config.from_object(Config)

    # Инициализируем ModelManager (синглтон)
    try:
        manager = ModelManager()
        app.config['MODEL_MANAGER'] = manager
        print("ModelManager инициализирован успешно")
    except Exception as e:
        print(f"Ошибка при инициализации ModelManager: {e}")
        app.config['MODEL_MANAGER'] = None

    # Регистрируем blueprint (будет в routes.py)
    from app.routes import main
    app.register_blueprint(main)

    return app