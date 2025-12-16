# config.py
import os
import sys
import secrets
from datetime import timedelta
from pathlib import Path
import warnings

# Подавление предупреждений о TripleDES
warnings.filterwarnings('ignore', category=DeprecationWarning, module='cryptography')
warnings.filterwarnings('ignore', message='TripleDES has been moved')

def init_config():
    """Инициализация конфигурации для Windows и Linux"""
    
    # Подавляем предупреждения paramiko
    import paramiko
    import logging
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    
    # Создаем директорию instance
    instance_path = Path('instance')
    instance_path.mkdir(exist_ok=True)
    
    # Путь к .env файлу
    env_path = instance_path / '.env'
    
    # Флаг первого запуска
    first_run = not env_path.exists()
    
    # Создаем .env если его нет
    if first_run:
        print("╔══════════════════════════════════════════════════════╗")
        print("║  🚀 ПЕРВЫЙ ЗАПУСК MIKROTIK UPDATE MANAGER            ║")
        print("╚══════════════════════════════════════════════════════╝")
        
        secret_key = secrets.token_hex(32)
        
        # Определяем DATABASE_URL в зависимости от ОС
        if sys.platform == 'win32':
            # Для Windows используем абсолютный путь с 4 слешами
            db_path = instance_path / 'app.db'
            abs_db_path = db_path.absolute()
            # Для Windows
            database_url = f'sqlite:///{abs_db_path}'.replace('\\', '/')
        else:
            # Для Linux/Mac
            database_url = 'sqlite:///instance/app.db'
        
        env_content = f"""# MikroTik Manager Configuration
# Файл создан автоматически

# Режим работы (production/development)
FLASK_ENV=development

# Секретный ключ для сессий
SECRET_KEY={secret_key}

# Хост и порт для веб-сервера
FLASK_HOST=0.0.0.0
FLASK_PORT=8923

# База данных
DATABASE_URL={database_url}
"""
        
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write(env_content)
        
        print("📁 Создан файл конфигурации: instance/.env")
        print("🔑 Секретный ключ сгенерирован автоматически")
        # Создаем .gitignore в instance
        gitignore_path = instance_path / '.gitignore'
        if not gitignore_path.exists():
            with open(gitignore_path, 'w', encoding='utf-8') as f:
                f.write("*\n!.gitignore\n")
    
    # Загружаем переменные окружения
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=env_path)
    
    # Получаем или генерируем секретный ключ
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key or secret_key == 'dev-secret-key-change-in-production':
        secret_key = secrets.token_hex(32)
        os.environ['SECRET_KEY'] = secret_key
    
    # Получаем DATABASE_URL из .env или используем умолчание
    db_url = os.environ.get('DATABASE_URL')
    
    if not db_url:
        # Генерируем URL базы данных по умолчанию
        if sys.platform == 'win32':
            db_path = instance_path / 'app.db'
            abs_db_path = db_path.absolute()
            db_url = f'sqlite:///{abs_db_path}'.replace('\\', '/')
        else:
            db_url = 'sqlite:///instance/app.db'
    
    # Конфигурация
    config = {
        'SECRET_KEY': secret_key,
        'SQLALCHEMY_DATABASE_URI': db_url,
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'SESSION_PERMANENT': True,
        'PERMANENT_SESSION_LIFETIME': timedelta(minutes=30),
        'SCHEDULER_API_ENABLED': True,
        'HOST': os.environ.get('FLASK_HOST', '0.0.0.0'),
        'PORT': int(os.environ.get('FLASK_PORT', 8923)),
        'INSTANCE_PATH': str(instance_path.absolute())
    }
    
    # Выводим информацию только при первом запуске
    if first_run:
        print("🗃  Создана база данных: instance/app.db")
        print("🔒 Логин по умолчанию: admin / MuMAdm123!")
        print("═" * 56)
    
    return config

# Инициализируем конфигурацию
config = init_config()

