import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB
    # Путь к папке с проектами пользователя (внутри storage)
    STORAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'storage')
    # Путь к папке для накопления тренировочных данных
    TRAINING_DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'training_data')
    # Принудительный CPU (можно переопределить в ModelManager)
    FORCE_CPU = os.environ.get('FORCE_CPU', 'False').lower() in ('true', '1', 't')