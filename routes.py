
# routes.py
from app import app, db, login_manager, scheduler
from flask import render_template, request, redirect, url_for, flash, jsonify, session, make_response
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from apscheduler.triggers.cron import CronTrigger
import json
from datetime import datetime, timedelta
import traceback
import paramiko
import re
from jinja2 import Environment

from database import User, Device, Task, DeviceLog
from mikrotik_manager import MikroTikManager
from decorators import admin_required, manager_or_admin_required

# ========== USER LOADER ==========
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ========== АУТЕНТИФИКАЦИЯ ==========
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('Пользователь деактивирован. Обратитесь к администратору.', 'error')
                return render_template('login.html')
            
            # Логируем успешный вход
            log = DeviceLog(
                device_id=None,
                action='user_login_success',
                result=json.dumps({
                    'status': 'success',
                    'user_id': user.id,
                    'username': user.username,
                    'ip_address': request.remote_addr
                }),
                details=f"Пользователь {user.username} успешно вошел в систему с IP {request.remote_addr}",
                performed_by=user.id
            )
            db.session.add(log)
            
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            login_user(user, remember=True)
            flash('Вход выполнен успешно!', 'success')
            return redirect(url_for('dashboard'))
        else:
            # Логируем неудачную попытку входа
            log = DeviceLog(
                device_id=None,
                action='user_login_failed',
                result=json.dumps({
                    'status': 'error',
                    'username': username,
                    'ip_address': request.remote_addr,
                    'reason': 'invalid_credentials'
                }),
                details=f"Неудачная попытка входа для пользователя {username} с IP {request.remote_addr}",
                performed_by=None
            )
            db.session.add(log)
            db.session.commit()
            
            flash('Неверное имя пользователя или пароль', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Выход из системы"""
    # Логируем выход
    log = DeviceLog(
        device_id=None,
        action='user_logout',
        result=json.dumps({
            'status': 'success',
            'user_id': current_user.id,
            'username': current_user.username
        }),
        details=f"Пользователь {current_user.username} вышел из системы",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))

# ========== ГЛАВНАЯ СТРАНИЦА ==========
@app.route('/')
@login_required
def dashboard():
    stats = {
        'total_devices': Device.query.count(),
        'online_devices': Device.query.filter_by(status='online').count(),
        'pending_updates': Device.query.filter_by(needs_update=True).count(),
        'active_tasks': Task.query.filter_by(is_active=True).count()
    }
    
    recent_logs = DeviceLog.query.order_by(DeviceLog.timestamp.desc()).limit(5).all()
    all_devices = Device.query.all()
    
    return render_template('dashboard.html', 
                         stats=stats, 
                         recent_logs=recent_logs,
                         all_devices=all_devices)

# ========== УСТРОЙСТВА ==========
@app.route('/devices')
@manager_or_admin_required
def devices():
    devices_list = Device.query.order_by(Device.name).all()
    return render_template('devices.html', devices=devices_list)

@app.route('/devices/add', methods=['GET', 'POST'])
@manager_or_admin_required
def add_device():
    if request.method == 'POST':
        try:
            existing = Device.query.filter_by(ip_address=request.form.get('ip_address')).first()
            if existing:
                flash('Устройство с таким IP адресом уже существует!', 'error')
                return render_template('add_device.html')
            
            device = Device(
                name=request.form.get('name'),
                ip_address=request.form.get('ip_address'),
                port=int(request.form.get('port', 22)),
                username=request.form.get('username'),
                password=request.form.get('password'),
                description=request.form.get('description'),
                is_encrypted=False,
                created_by=current_user.id
            )
            
            db.session.add(device)
            db.session.commit()
           
            test_result = MikroTikManager.test_connection(device)
            if test_result['status'] == 'success':
                device.status = 'online'
            else:
                device.status = 'offline'
            
            log = DeviceLog(
                device_id=device.id,
                action='device_added',
                result=json.dumps(test_result),
                details=f"Добавлено устройство {device.name} ({device.ip_address})",
                performed_by=current_user.id
            )
            db.session.add(log)
            db.session.commit()
            
            flash('Устройство успешно добавлено!', 'success')
            return redirect(url_for('devices'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при добавлении устройства: {str(e)}', 'error')
    
    return render_template('add_device.html')

@app.route('/devices/<int:device_id>/edit', methods=['GET', 'POST'])
@manager_or_admin_required
def edit_device(device_id):
    device = Device.query.get_or_404(device_id)
    
    if request.method == 'POST':
        try:
            old_data = {
                'name': device.name,
                'ip_address': device.ip_address,
                'port': device.port,
                'username': device.username
            }
            
            device.name = request.form.get('name')
            device.ip_address = request.form.get('ip_address')
            device.port = int(request.form.get('port', 22))
            device.username = request.form.get('username')
            device.password = request.form.get('password')
            device.description = request.form.get('description')
            
            db.session.commit()
            
            # Логируем изменения
            changes = []
            if old_data['name'] != device.name:
                changes.append(f"Имя: {old_data['name']} → {device.name}")
            if old_data['ip_address'] != device.ip_address:
                changes.append(f"IP: {old_data['ip_address']} → {device.ip_address}")
            if old_data['port'] != device.port:
                changes.append(f"Порт: {old_data['port']} → {device.port}")
            if old_data['username'] != device.username:
                changes.append(f"Пользователь: {old_data['username']} → {device.username}")
            
            log = DeviceLog(
                device_id=device.id,
                action='device_edited',
                result=json.dumps({
                    'status': 'success',
                    'changes': changes
                }),
                details=f"Изменено устройство {device.name} (ID: {device.id}). Изменения: {', '.join(changes)}",
                performed_by=current_user.id
            )
            db.session.add(log)
            db.session.commit()
            
            flash('Устройство обновлено!', 'success')
            return redirect(url_for('devices'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при обновлении устройства: {str(e)}', 'error')
    
    return render_template('edit_device.html', device=device)

@app.route('/devices/<int:device_id>/delete', methods=['POST'])
@manager_or_admin_required
def delete_device(device_id):
    device = Device.query.get_or_404(device_id)
    
    # Логируем удаление
    log = DeviceLog(
        device_id=device.id,
        action='device_deleted',
        result=json.dumps({
            'status': 'success',
            'device_name': device.name,
            'device_ip': device.ip_address
        }),
        details=f"Удалено устройство {device.name} ({device.ip_address})",
        performed_by=current_user.id
    )
    db.session.add(log)
    
    DeviceLog.query.filter_by(device_id=device_id).delete()
    
    tasks = Task.query.all()
    for task in tasks:
        device_ids = task.get_device_ids()
        if device_id in device_ids:
            device_ids.remove(device_id)
            task.set_device_ids(device_ids)
    
    db.session.delete(device)
    db.session.commit()
    
    flash('Устройство удалено', 'success')
    return redirect(url_for('devices'))

# ========== ТЕСТ ПОДКЛЮЧЕНИЯ ==========
@app.route('/devices/<int:device_id>/test')
@manager_or_admin_required
def test_device_connection(device_id):
    device = Device.query.get_or_404(device_id)
    result = MikroTikManager.test_connection(device)
    
    log = DeviceLog(
        device_id=device.id,
        action='device_test_connection',
        result=json.dumps(result),
        details=f"Тест подключения к устройству {device.name} ({device.ip_address})",
        performed_by=current_user.id
    )
    db.session.add(log)
    
    if result['status'] == 'success':
        # При успешном тесте получаем полную информацию
        system_info = MikroTikManager.get_system_info(device)
        
        if system_info['status'] == 'success':
            device.status = 'online'
            device.firmware_version = system_info.get('version')
            flash(f'Подключение успешно! Версия RouterOS: {system_info.get("version")}', 'success')
        else:
            device.status = 'online'
            flash('Подключение успешно, но не удалось получить версию', 'warning')
    else:
        device.status = 'offline'
        flash(f'Ошибка подключения: {result["message"]}', 'error')
    
    db.session.commit()
    return redirect(url_for('devices'))

# ========== ИНФОРМАЦИЯ О СИСТЕМЕ ==========
@app.route('/devices/<int:device_id>/system-info')
@manager_or_admin_required
def get_device_system_info(device_id):
    """Получение подробной информации о системе устройства"""
    device = Device.query.get_or_404(device_id)
    
    # Используем расширенный метод
    result = MikroTikManager.get_extended_system_info(device)
    
    log = DeviceLog(
        device_id=device.id,
        action='device_system_info',
        result=json.dumps({'status': result['status']}),
        details=f"Получение системной информации устройства {device.name}",
        performed_by=current_user.id
    )
    db.session.add(log)
    
    if result['status'] == 'success':
        device.firmware_version = result.get('basic', {}).get('version')
        device.status = 'online'
        db.session.commit()
        
        return render_template('system_info.html', 
                             device=device,
                             info=result)
    else:
        device.status = 'offline'
        db.session.commit()
        
        flash(f'Ошибка получения информации: {result["message"]}', 'error')
        return redirect(url_for('devices'))

# ========== ПРОВЕРКА ОБНОВЛЕНИЙ ==========
@app.route('/devices/<int:device_id>/check-update')
@manager_or_admin_required
def check_device_update(device_id):
    device = Device.query.get_or_404(device_id)
    result = MikroTikManager.check_for_updates(device)
    
    if result['status'] == 'success':
        device.last_check = datetime.utcnow()
        device.firmware_version = result.get('current_version')
        device.status = 'online'
        
        # Определяем, нужны ли обновления
        if result.get('has_updates', False):
            device.needs_update = True
            update_status = 'available'
        else:
            device.needs_update = False
            update_status = 'latest'
        
        # Логируем действие
        log = DeviceLog(
            device_id=device.id,
            action='update_check',
            result=json.dumps({
                'status': 'success',
                'version': result.get('current_version'),
                'update_status': update_status,
                'has_updates': result.get('has_updates', False)
            }),
            details=f"Проверка обновлений для устройства {device.name}. Статус: {update_status}",
            performed_by=current_user.id
        )
        db.session.add(log)
        db.session.commit()
        
        flash('Проверка обновлений выполнена успешно!', 'success')
    else:
        device.status = 'offline'
        device.needs_update = False
        flash(f'Ошибка: {result["message"]}', 'error')
    
    db.session.commit()
    return redirect(url_for('devices'))

# ========== ВЫПОЛНЕНИЕ ОБНОВЛЕНИЯ ==========
@app.route('/devices/<int:device_id>/perform-update')
@manager_or_admin_required
def perform_device_update(device_id):
    device = Device.query.get_or_404(device_id)
    
    if 'confirmed' not in request.args:
        return render_template('confirm_update.html', device=device)
    
    create_backup = request.args.get('backup', 'true').lower() == 'true'
    
    if create_backup:
        result = MikroTikManager.perform_update_with_backup(device, create_backup=True)
    else:
        result = MikroTikManager.perform_update(device)
    
    log = DeviceLog(
        device_id=device.id,
        action='update_performed',
        result=json.dumps({
            'status': result['status'],
            'with_backup': create_backup,
            'message': result.get('message', '')
        }),
        details=f"Выполнение обновления устройства {device.name}. С бэкапом: {create_backup}",
        performed_by=current_user.id
    )
    db.session.add(log)
    
    if result['status'] == 'success':
        device.last_update = datetime.utcnow()
        device.needs_update = False
        flash('Обновление выполнено успешно! Устройство будет перезагружено.', 'success')
    else:
        flash(f'Ошибка: {result["message"]}', 'error')
    
    db.session.commit()
    return redirect(url_for('devices'))

# ========== УПРАВЛЕНИЕ БЭКАПАМИ ==========
@app.route('/devices/<int:device_id>/backups')
@manager_or_admin_required
def device_backups(device_id):
    """Просмотр резервных копий устройства"""
    device = Device.query.get_or_404(device_id)
    
    result = MikroTikManager.list_backups(device)
    
    log = DeviceLog(
        device_id=device.id,
        action='device_backups_list',
        result=json.dumps({'status': result['status']}),
        details=f"Просмотр бэкапов устройства {device.name}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    if result['status'] == 'success':
        return render_template('device_backups.html', 
                             device=device,
                             backups=result.get('backups', []),
                             backup_count=result.get('count', 0))
    else:
        flash(f'Ошибка получения бэкапов: {result["message"]}', 'error')
        return redirect(url_for('get_device_system_info', device_id=device_id))

@app.route('/devices/<int:device_id>/create-backup', methods=['POST'])
@manager_or_admin_required
def create_device_backup(device_id):
    """Создание резервной копии устройства"""
    device = Device.query.get_or_404(device_id)
    
    result = MikroTikManager.create_backup(device)
    
    log = DeviceLog(
        device_id=device.id,
        action='device_backup_created',
        result=json.dumps(result),
        details=f"Создание бэкапа устройства {device.name}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    if result['status'] == 'success':
        flash(f'Резервная копия создана: {result.get("backup_name", "")}', 'success')
    else:
        flash(f'Ошибка создания бэкапа: {result["message"]}', 'error')
    
    return redirect(url_for('device_backups', device_id=device_id))

@app.route('/devices/<int:device_id>/delete-backup/<backup_name>', methods=['POST'])
@manager_or_admin_required
def delete_device_backup(device_id, backup_name):
    """Удаление резервной копии"""
    device = Device.query.get_or_404(device_id)
    
    result = MikroTikManager.delete_backup(device, backup_name)
    
    log = DeviceLog(
        device_id=device.id,
        action='device_backup_deleted',
        result=json.dumps(result),
        details=f"Удаление бэкапа {backup_name} устройства {device.name}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    if result['status'] == 'success':
        flash(f'Резервная копия удалена: {backup_name}', 'success')
    else:
        flash(f'Ошибка удаления бэкапа: {result["message"]}', 'error')
    
    return redirect(url_for('device_backups', device_id=device_id))

@app.route('/devices/<int:device_id>/cleanup-backups', methods=['POST'])
@manager_or_admin_required
def cleanup_device_backups(device_id):
    """Очистка старых резервных копий"""
    device = Device.query.get_or_404(device_id)
    
    result = MikroTikManager.delete_old_backups(device, keep_count=5)
    
    log = DeviceLog(
        device_id=device.id,
        action='device_backups_cleaned',
        result=json.dumps(result),
        details=f"Очистка старых бэкапов устройства {device.name}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    if result['status'] == 'success':
        deleted_count = len(result.get('deleted', []))
        flash(f'Удалено {deleted_count} старых бэкапов', 'success')
    else:
        flash(f'Ошибка очистки бэкапов: {result["message"]}', 'error')
    
    return redirect(url_for('device_backups', device_id=device_id))

# ========== МАССОВЫЕ ОПЕРАЦИИ ==========
@app.route('/batch-check', methods=['GET', 'POST'])
@manager_or_admin_required
def batch_check():
    if request.method == 'GET':
        devices = Device.query.all()
        return render_template('batch_check_form.html', devices=devices, action='check')
    
    device_ids = request.form.getlist('device_ids')
    results = []
    
    for device_id in device_ids:
        device = Device.query.get(device_id)
        if device:
            result = MikroTikManager.check_for_updates(device)
            
            if result['status'] == 'success':
                device.last_check = datetime.utcnow()
                device.firmware_version = result.get('current_version')
                device.status = 'online'
                
                if result.get('has_updates', False):
                    device.needs_update = True
                else:
                    device.needs_update = False
                
                log = DeviceLog(
                    device_id=device.id,
                    action='batch_check',
                    result=json.dumps({
                        'status': 'success',
                        'version': result.get('current_version'),
                        'has_updates': result.get('has_updates', False)
                    }),
                    details=f"Массовая проверка обновлений устройства {device.name}",
                    performed_by=current_user.id
                )
                db.session.add(log)
            else:
                device.status = 'offline'
                device.needs_update = False
            
            results.append({
                'device': device,
                'result': result
            })
    
    db.session.commit()
    
    success_count = sum(1 for r in results if r['result']['status'] == 'success')
    error_count = sum(1 for r in results if r['result']['status'] == 'error')
    update_count = sum(1 for r in results if r['result'].get('has_updates', False))
    
    # Логируем общий результат массовой проверки
    log = DeviceLog(
        device_id=None,
        action='batch_check_completed',
        result=json.dumps({
            'status': 'success',
            'total': len(device_ids),
            'success': success_count,
            'error': error_count,
            'updates': update_count
        }),
        details=f"Завершена массовая проверка обновлений. Успешно: {success_count}, Ошибок: {error_count}, Обновлений доступно: {update_count}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    return render_template('batch_check_results.html', 
                         results=results, 
                         now=datetime.now(),
                         success_count=success_count,
                         error_count=error_count,
                         update_count=update_count,
                         action='check')

@app.route('/batch-update', methods=['GET', 'POST'])
@manager_or_admin_required
def batch_update():
    if request.method == 'GET':
        devices = Device.query.filter_by(needs_update=True).all()
        return render_template('batch_update_form.html', devices=devices, action='update')
    
    device_ids = request.form.getlist('device_ids')
    create_backup = request.form.get('create_backup', 'true').lower() == 'true'
    confirmed = request.form.get('confirmed', False)
    
    if not confirmed:
        devices_to_update = Device.query.filter(Device.id.in_(device_ids)).all()
        return render_template('batch_update_confirm.html', 
                             devices=devices_to_update,
                             device_ids=device_ids,
                             create_backup=create_backup)
    
    results = []
    for device_id in device_ids:
        device = Device.query.get(device_id)
        if device:
            if create_backup:
                result = MikroTikManager.perform_update_with_backup(device, create_backup=True)
            else:
                result = MikroTikManager.perform_update(device)
            
            if result['status'] == 'success':
                device.last_update = datetime.utcnow()
                device.needs_update = False
                
                log = DeviceLog(
                    device_id=device.id,
                    action='batch_update_performed',
                    result=json.dumps({
                        'status': 'success',
                        'with_backup': create_backup,
                        'message': result.get('message', '')
                    }),
                    details=f"Массовое обновление устройства {device.name}",
                    performed_by=current_user.id
                )
                db.session.add(log)
            
            results.append({
                'device': device,
                'result': result
            })
    
    db.session.commit()
    
    success_count = sum(1 for r in results if r['result']['status'] == 'success')
    error_count = sum(1 for r in results if r['result']['status'] == 'error')
    
    # Логируем общий результат массового обновления
    log = DeviceLog(
        device_id=None,
        action='batch_update_completed',
        result=json.dumps({
            'status': 'success',
            'total': len(device_ids),
            'success': success_count,
            'error': error_count,
            'with_backup': create_backup
        }),
        details=f"Завершено массовое обновление. Успешно: {success_count}, Ошибок: {error_count}, С бэкапом: {create_backup}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    return render_template('batch_update_results.html', 
                         results=results,
                         now=datetime.now(),
                         success_count=success_count,
                         error_count=error_count,
                         with_backup=create_backup)

# ========== ЗАДАЧИ ==========
def schedule_task(task):
    """Добавление задачи в планировщик"""
    def task_wrapper():
        with app.app_context():
            execute_task(task.id)
    
    if task.cron_expression:
        try:
            trigger = CronTrigger.from_crontab(task.cron_expression)
            scheduler.add_job(
                func=task_wrapper,
                trigger=trigger,
                id=f'task_{task.id}',
                replace_existing=True,
                misfire_grace_time=60
            )
        except Exception as e:
            print(f"Ошибка планирования задачи {task.id}: {str(e)}")

def execute_task(task_id):
    """Выполнение запланированной задачи"""
    task = Task.query.get(task_id)
    if not task or not task.is_active:
        return
    
    results = []
    device_ids = task.get_device_ids()
    
    for device_id in device_ids:
        device = Device.query.get(device_id)
        if device:
            if task.task_type == 'check_update':
                result = MikroTikManager.check_for_updates(device)
            elif task.task_type == 'perform_update':
                result = MikroTikManager.perform_update(device)
            elif task.task_type == 'custom_command' and task.command:
                ssh = MikroTikManager.connect_to_device(device)
                if ssh:
                    cmd_result = MikroTikManager.execute_command(ssh, task.command)
                    ssh.close()
                    result = {'status': 'success' if cmd_result['success'] else 'error', 
                             'output': cmd_result.get('output', cmd_result.get('error'))}
                else:
                    result = {'status': 'error', 'message': 'Connection failed'}
            else:
                result = {'status': 'error', 'message': 'Unknown task type'}
            
            results.append({
                'device': device.name,
                'result': result
            })
            
            log = DeviceLog(
                device_id=device.id,
                action=f'task_{task.task_type}',
                result=json.dumps(result),
                details=f"Выполнение задачи '{task.name}' для устройства {device.name}",
                performed_by=task.created_by
            )
            db.session.add(log)
    
    task.last_run = datetime.utcnow()
    task.last_result = json.dumps(results)
    
    # Логируем выполнение задачи
    log = DeviceLog(
        device_id=None,
        action='task_executed',
        result=json.dumps({
            'task_id': task.id,
            'task_name': task.name,
            'task_type': task.task_type,
            'results': results
        }),
        details=f"Выполнена задача '{task.name}'. Затронуто устройств: {len(device_ids)}",
        performed_by=task.created_by
    )
    db.session.add(log)
    
    db.session.commit()

@app.route('/tasks')
@manager_or_admin_required
def tasks():
    tasks_list = Task.query.order_by(Task.created_at.desc()).all()
    return render_template('tasks.html', tasks=tasks_list)

@app.route('/tasks/add', methods=['GET', 'POST'])
@manager_or_admin_required
def add_task():
    if request.method == 'POST':
        try:
            device_ids = request.form.getlist('device_ids')
            
            task = Task(
                name=request.form.get('name'),
                task_type=request.form.get('task_type'),
                command=request.form.get('command', ''),
                cron_expression=request.form.get('cron_expression'),
                is_active=bool(request.form.get('is_active')),
                created_by=current_user.id
            )
            task.set_device_ids([int(id) for id in device_ids])
            
            db.session.add(task)
            db.session.commit()
            
            # Логируем создание задачи
            log = DeviceLog(
                device_id=None,
                action='task_created',
                result=json.dumps({
                    'status': 'success',
                    'task_id': task.id,
                    'task_name': task.name,
                    'task_type': task.task_type,
                    'device_count': len(device_ids)
                }),
                details=f"Создана задача '{task.name}' типа {task.task_type}. Затронуто устройств: {len(device_ids)}",
                performed_by=current_user.id
            )
            db.session.add(log)
            db.session.commit()
            
            if task.is_active and task.cron_expression:
                schedule_task(task)
            
            flash('Задача успешно добавлена!', 'success')
            return redirect(url_for('tasks'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при добавлении задачи: {str(e)}', 'error')
    
    devices = Device.query.all()
    return render_template('add_task.html', devices=devices)

@app.route('/tasks/<int:task_id>/run-now', methods=['POST'])
@manager_or_admin_required
def run_task_now(task_id):
    task = Task.query.get_or_404(task_id)
    try:
        execute_task(task_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/tasks/<int:task_id>/<action>', methods=['POST'])
@manager_or_admin_required
def toggle_task_status(task_id, action):
    task = Task.query.get_or_404(task_id)
    
    if action == 'activate':
        task.is_active = True
        if task.cron_expression:
            schedule_task(task)
        message = 'Задача активирована'
    elif action == 'deactivate':
        task.is_active = False
        try:
            scheduler.remove_job(f'task_{task.id}')
        except:
            pass
        message = 'Задача деактивирована'
    else:
        return jsonify({'success': False, 'error': 'Неизвестное действие'})
    
    db.session.commit()
    
    # Логируем изменение статуса задачи
    log = DeviceLog(
        device_id=None,
        action='task_status_changed',
        result=json.dumps({
            'status': 'success',
            'task_id': task.id,
            'task_name': task.name,
            'new_status': action
        }),
        details=f"Задача '{task.name}' {'активирована' if action == 'activate' else 'деактивирована'}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({'success': True, 'message': message})

@app.route('/tasks/<int:task_id>/details')
@manager_or_admin_required
def task_details(task_id):
    task = Task.query.get_or_404(task_id)
    devices = Device.query.filter(Device.id.in_(task.get_device_ids())).all()
    
    return render_template('task_details.html', task=task, devices=devices)

@app.route('/tasks/<int:task_id>/delete', methods=['POST'])
@manager_or_admin_required
def delete_task(task_id):
    task = Task.query.get_or_404(task_id)
    
    try:
        # Логируем удаление задачи
        log = DeviceLog(
            device_id=None,
            action='task_deleted',
            result=json.dumps({
                'status': 'success',
                'task_id': task.id,
                'task_name': task.name,
                'task_type': task.task_type
            }),
            details=f"Удалена задача '{task.name}'",
            performed_by=current_user.id
        )
        db.session.add(log)
        
        try:
            scheduler.remove_job(f'task_{task.id}')
        except:
            pass
        
        db.session.delete(task)
        db.session.commit()
        
        return jsonify({'success': True})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

# ========== ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ ==========
@app.route('/profile')
@login_required
def profile():
    """Страница профиля пользователя"""
    # Подсчет статистики
    created_devices_count = Device.query.filter_by(created_by=current_user.id).count()
    created_tasks_count = Task.query.filter_by(created_by=current_user.id).count()
    log_count = DeviceLog.query.filter_by(performed_by=current_user.id).count()
    
    # Дни в системе
    user_days = (datetime.utcnow() - current_user.created_at).days
    
    # Последние действия пользователя
    user_logs = DeviceLog.query.filter_by(
        performed_by=current_user.id
    ).order_by(DeviceLog.timestamp.desc()).limit(10).all()
    
    return render_template('profile.html',
                         created_devices_count=created_devices_count,
                         created_tasks_count=created_tasks_count,
                         log_count=log_count,
                         user_days=user_days,
                         user_logs=user_logs)

@app.route('/profile/update', methods=['POST'])
@login_required
def update_profile():
    """Обновление профиля пользователя"""
    try:
        old_data = {
            'full_name': current_user.full_name,
            'email': current_user.email,
            'phone': current_user.phone
        }
        
        # Обновляем основные данные
        current_user.full_name = request.form.get('full_name')
        current_user.email = request.form.get('email')
        current_user.phone = request.form.get('phone')
        
        # Смена пароля
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        current_password = request.form.get('current_password')
        
        password_changed = False
        if new_password and confirm_password:
            if new_password != confirm_password:
                flash('Пароли не совпадают', 'error')
                return redirect(url_for('profile'))
            
            if not current_password or not check_password_hash(current_user.password_hash, current_password):
                flash('Неверный текущий пароль', 'error')
                return redirect(url_for('profile'))
            
            current_user.password_hash = generate_password_hash(new_password)
            password_changed = True
            flash('Пароль успешно изменен', 'success')
        
        db.session.commit()
        
        # Логируем изменение профиля
        changes = []
        if old_data['full_name'] != current_user.full_name:
            changes.append(f"Полное имя: {old_data['full_name'] or 'нет'} → {current_user.full_name or 'нет'}")
        if old_data['email'] != current_user.email:
            changes.append(f"Email: {old_data['email'] or 'нет'} → {current_user.email or 'нет'}")
        if old_data['phone'] != current_user.phone:
            changes.append(f"Телефон: {old_data['phone'] or 'нет'} → {current_user.phone or 'нет'}")
        if password_changed:
            changes.append("Пароль изменен")
        
        if changes:
            log = DeviceLog(
                device_id=None,
                action='profile_updated',
                result=json.dumps({
                    'status': 'success',
                    'user_id': current_user.id,
                    'username': current_user.username,
                    'changed_fields': changes
                }),
                details=f"Пользователь {current_user.username} обновил свой профиль. Изменения: {', '.join(changes)}",
                performed_by=current_user.id
            )
            db.session.add(log)
            db.session.commit()
        
        flash('Профиль успешно обновлен', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при обновлении профиля: {str(e)}', 'error')
    
    return redirect(url_for('profile'))

# ========== УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (ТОЛЬКО ДЛЯ АДМИНОВ) ==========
@app.route('/users')
@admin_required
def users():
    """Список всех пользователей (только для админов)"""
    users_list = User.query.order_by(User.created_at.desc()).all()
    
    stats = {
        'total_users': User.query.count(),
        'admins_count': User.query.filter_by(role='admin').count(),
        'managers_count': User.query.filter_by(role='manager').count(),
        'inactive_users': User.query.filter_by(is_active=False).count()
    }
    
    return render_template('users.html', users=users_list, stats=stats)

@app.route('/users/add', methods=['GET', 'POST'])
@admin_required
def add_user():
    """Добавление нового пользователя (только для админов)"""
    if request.method == 'POST':
        try:
            # Проверка уникальности имени пользователя
            existing_user = User.query.filter_by(username=request.form.get('username')).first()
            if existing_user:
                flash('Пользователь с таким именем уже существует', 'error')
                return render_template('add_user.html', form_data=request.form)
            
            # Проверка пароля
            password = request.form.get('password')
            confirm_password = request.form.get('confirm_password')
            
            if password != confirm_password:
                flash('Пароли не совпадают', 'error')
                return render_template('add_user.html', form_data=request.form)
            
            if len(password) < 8:
                flash('Пароль должен содержать минимум 8 символов', 'error')
                return render_template('add_user.html', form_data=request.form)
            
            # Создание пользователя
            user = User(
                username=request.form.get('username'),
                password_hash=generate_password_hash(password),
                full_name=request.form.get('full_name'),
                email=request.form.get('email'),
                phone=request.form.get('phone'),
                role=request.form.get('role'),
                is_active=bool(request.form.get('is_active')),
                created_by=current_user.id
            )
            
            db.session.add(user)
            db.session.commit()
            
            # Логируем создание пользователя
            log = DeviceLog(
                device_id=None,
                action='user_created',
                result=json.dumps({
                    'status': 'success',
                    'user_id': user.id,
                    'username': user.username,
                    'role': user.role,
                    'created_by': current_user.id
                }),
                details=f"Создан пользователь {user.username} (ID: {user.id}) с ролью {user.role}",
                performed_by=current_user.id
            )
            db.session.add(log)
            db.session.commit()
            
            flash(f'Пользователь {user.username} успешно создан!', 'success')
            return redirect(url_for('users'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при создании пользователя: {str(e)}', 'error')
    
    return render_template('add_user.html')

@app.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    """Редактирование пользователя"""
    editing_user = User.query.get_or_404(user_id)
    
    # Проверка прав: пользователь может редактировать себя, админ - любого
    if current_user.id != user_id and current_user.role != 'admin':
        flash('У вас нет прав для редактирования этого пользователя', 'error')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        try:
            old_data = {
                'username': editing_user.username,
                'full_name': editing_user.full_name,
                'email': editing_user.email,
                'phone': editing_user.phone,
                'role': editing_user.role,
                'is_active': editing_user.is_active
            }
            
            # Проверка уникальности имени пользователя (если изменилось)
            new_username = request.form.get('username')
            if new_username != editing_user.username:
                existing = User.query.filter_by(username=new_username).first()
                if existing and existing.id != user_id:
                    flash('Пользователь с таким именем уже существует', 'error')
                    return render_template('edit_user.html', editing_user=editing_user)
                editing_user.username = new_username
            
            # Обновление данных
            editing_user.full_name = request.form.get('full_name')
            editing_user.email = request.form.get('email')
            editing_user.phone = request.form.get('phone')
            
            # Только админ может менять роль и статус
            if current_user.role == 'admin' and current_user.id != user_id:
                editing_user.role = request.form.get('role')
                editing_user.is_active = bool(request.form.get('is_active'))
            
            # Смена пароля
            password_changed = False
            if current_user.id == user_id:
                # Пользователь меняет свой пароль
                current_password = request.form.get('current_password')
                new_password = request.form.get('new_password')
                confirm_password = request.form.get('confirm_password')
                
                if new_password:
                    if not current_password or not check_password_hash(editing_user.password_hash, current_password):
                        flash('Неверный текущий пароль', 'error')
                        return render_template('edit_user.html', editing_user=editing_user)
                    
                    if new_password != confirm_password:
                        flash('Пароли не совпадают', 'error')
                        return render_template('edit_user.html', editing_user=editing_user)
                    
                    editing_user.password_hash = generate_password_hash(new_password)
                    password_changed = True
            elif current_user.role == 'admin':
                # Админ меняет пароль другого пользователя
                new_password = request.form.get('new_password')
                confirm_password = request.form.get('confirm_password')
                
                if new_password:
                    if new_password != confirm_password:
                        flash('Пароли не совпадают', 'error')
                        return render_template('edit_user.html', editing_user=editing_user)
                    
                    editing_user.password_hash = generate_password_hash(new_password)
                    password_changed = True
            
            db.session.commit()
            
            # Логируем изменение пользователя
            changes = []
            if old_data['username'] != editing_user.username:
                changes.append(f"Имя пользователя: {old_data['username']} → {editing_user.username}")
            if old_data['full_name'] != editing_user.full_name:
                changes.append(f"Полное имя: {old_data['full_name'] or 'нет'} → {editing_user.full_name or 'нет'}")
            if old_data['email'] != editing_user.email:
                changes.append(f"Email: {old_data['email'] or 'нет'} → {editing_user.email or 'нет'}")
            if old_data['phone'] != editing_user.phone:
                changes.append(f"Телефон: {old_data['phone'] or 'нет'} → {editing_user.phone or 'нет'}")
            if old_data['role'] != editing_user.role:
                changes.append(f"Роль: {old_data['role']} → {editing_user.role}")
            if old_data['is_active'] != editing_user.is_active:
                changes.append(f"Статус: {'активен' if old_data['is_active'] else 'неактивен'} → {'активен' if editing_user.is_active else 'неактивен'}")
            if password_changed:
                changes.append("Пароль изменен")
            
            if changes:
                log = DeviceLog(
                    device_id=None,
                    action='user_edited',
                    result=json.dumps({
                        'status': 'success',
                        'user_id': editing_user.id,
                        'username': editing_user.username,
                        'changed_fields': changes
                    }),
                    details=f"Изменен пользователь {editing_user.username} (ID: {editing_user.id}). Изменения: {', '.join(changes)}",
                    performed_by=current_user.id
                )
                db.session.add(log)
                db.session.commit()
            
            flash('Пользователь успешно обновлен', 'success')
            
            if current_user.id == user_id:
                return redirect(url_for('profile'))
            else:
                return redirect(url_for('users'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка при обновлении пользователя: {str(e)}', 'error')
    
    return render_template('edit_user.html', editing_user=editing_user)

@app.route('/users/<int:user_id>/<action>', methods=['POST'])
@admin_required
def toggle_user_status(user_id, action):
    """Активация/деактивация пользователя (только для админов)"""
    if user_id == current_user.id:
        return jsonify({'success': False, 'error': 'Нельзя изменить статус себе'})
    
    user = User.query.get_or_404(user_id)
    
    if action == 'activate':
        user.is_active = True
        message = 'Пользователь активирован'
    elif action == 'deactivate':
        user.is_active = False
        message = 'Пользователь деактивирован'
    else:
        return jsonify({'success': False, 'error': 'Неизвестное действие'})
    
    try:
        db.session.commit()
        
        # Логируем изменение статуса
        log = DeviceLog(
            device_id=None,
            action='user_status_changed',
            result=json.dumps({
                'status': 'success',
                'user_id': user.id,
                'username': user.username,
                'new_status': 'active' if action == 'activate' else 'inactive',
                'action': action
            }),
            details=f"Пользователь {user.username} (ID: {user.id}) {'активирован' if action == 'activate' else 'деактивирован'}",
            performed_by=current_user.id
        )
        db.session.add(log)
        db.session.commit()
        
        return jsonify({'success': True, 'message': message})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Удаление пользователя (только для админов)"""
    if user_id == current_user.id:
        return jsonify({'success': False, 'error': 'Нельзя удалить себя'})
    
    user = User.query.get_or_404(user_id)
    
    try:
        # Логируем информацию о пользователе перед удалением
        user_info = {
            'id': user.id,
            'username': user.username,
            'full_name': user.full_name,
            'email': user.email,
            'role': user.role,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'created_by': user.created_by,
            'devices_count': Device.query.filter_by(created_by=user_id).count(),
            'tasks_count': Task.query.filter_by(created_by=user_id).count()
        }
        
        # Передача устройств и задач текущему админу
        devices_updated = Device.query.filter_by(created_by=user_id).update({'created_by': current_user.id})
        tasks_updated = Task.query.filter_by(created_by=user_id).update({'created_by': current_user.id})
        
        # Удаление пользователя
        db.session.delete(user)
        db.session.commit()
        
        # Логируем удаление пользователя
        log = DeviceLog(
            device_id=None,
            action='user_deleted',
            result=json.dumps({
                'status': 'success',
                'user_info': user_info,
                'devices_transferred': devices_updated,
                'tasks_transferred': tasks_updated,
                'transferred_to': current_user.id
            }),
            details=f"Удален пользователь {user_info['username']} (ID: {user_info['id']}). " +
                   f"Передано устройств: {devices_updated}, задач: {tasks_updated}.",
            performed_by=current_user.id
        )
        db.session.add(log)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Пользователь удален'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})

# ========== ЛОГИ ==========
@app.route('/logs')
@manager_or_admin_required
def logs():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    logs_query = DeviceLog.query.order_by(DeviceLog.timestamp.desc())
    logs_paginated = logs_query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Получаем всех пользователей для фильтра
    all_users = User.query.order_by(User.username).all()
    
    return render_template('logs.html', 
                         logs=logs_paginated.items,
                         all_users=all_users,
                         page=page,
                         total_pages=logs_paginated.pages)

@app.route('/logs/<int:log_id>/details')
@manager_or_admin_required
def log_details(log_id):
    log = DeviceLog.query.get_or_404(log_id)
    return render_template('log_details.html', log=log)

@app.route('/logs/clear', methods=['POST'])
@admin_required
def clear_logs():
    month_ago = datetime.utcnow() - timedelta(days=30)
    deleted_count = DeviceLog.query.filter(DeviceLog.timestamp < month_ago).count()
    
    DeviceLog.query.filter(DeviceLog.timestamp < month_ago).delete()
    db.session.commit()
    
    # Логируем очистку логов
    log = DeviceLog(
        device_id=None,
        action='logs_cleared',
        result=json.dumps({
            'status': 'success',
            'deleted_count': deleted_count,
            'older_than': month_ago.isoformat()
        }),
        details=f"Очищено логов старше 30 дней. Удалено записей: {deleted_count}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    flash(f'Очищено {deleted_count} старых логов', 'success')
    return jsonify({'success': True, 'deleted_count': deleted_count})

# ========== API ==========
@app.route('/api/device/<int:device_id>/status')
@manager_or_admin_required
def device_status(device_id):
    device = Device.query.get_or_404(device_id)
    result = MikroTikManager.test_connection(device)
    
    log = DeviceLog(
        device_id=device.id,
        action='device_status_api',
        result=json.dumps({'status': result['status']}),
        details=f"API запрос статуса устройства {device.name}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify(result)

@app.route('/api/users')
@login_required
def get_users_api():
    """API для получения списка пользователей (для фильтров)"""
    users = User.query.order_by(User.username).all()
    return jsonify([{
        'id': user.id,
        'username': user.username,
        'role': user.role
    } for user in users])

# ========== ОТЛАДКА ==========
@app.route('/devices/<int:device_id>/debug')
@manager_or_admin_required
def debug_device_connection(device_id):
    """Отладка подключения к устройству"""
    device = Device.query.get_or_404(device_id)
    
    log = DeviceLog(
        device_id=device.id,
        action='device_debug',
        result=json.dumps({'status': 'started'}),
        details=f"Запущена отладка подключения к устройству {device.name}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        debug_info = {
            'device': {
                'name': device.name,
                'ip': device.ip_address,
                'port': device.port,
                'username': device.username
            },
            'steps': []
        }
        
        debug_info['steps'].append({
            'step': 'Подключение',
            'action': f'Попытка подключиться к {device.ip_address}:{device.port}'
        })
        
        client.connect(
            hostname=device.ip_address,
            port=device.port,
            username=device.username,
            password=device.password,
            timeout=10,
            banner_timeout=15,
            auth_timeout=10
        )
        
        debug_info['steps'].append({
            'step': 'Подключение',
            'result': 'Успешно',
            'details': 'SSH подключение установлено'
        })
        
        debug_info['steps'].append({
            'step': 'Тестовая команда',
            'action': 'Выполнение команды: /system identity print'
        })
        
        stdin, stdout, stderr = client.exec_command('/system identity print')
        identity_output = stdout.read().decode('utf-8', errors='ignore')
        error_output = stderr.read().decode('utf-8', errors='ignore')
        
        debug_info['steps'].append({
            'step': 'Тестовая команда',
            'result': 'Успешно' if not error_output else 'Ошибка',
            'output': identity_output[:200],
            'error': error_output
        })
        
        debug_info['steps'].append({
            'step': 'Информация о системе',
            'action': 'Выполнение команды: /system resource print'
        })
        
        stdin, stdout, stderr = client.exec_command('/system resource print')
        resource_output = stdout.read().decode('utf-8', errors='ignore')
        resource_error = stderr.read().decode('utf-8', errors='ignore')
        
        debug_info['steps'].append({
            'step': 'Информация о системе',
            'result': 'Успешно' if not resource_error else 'Ошибка',
            'output': resource_output,
            'error': resource_error
        })
        
        debug_info['steps'].append({
            'step': 'Информация о пакетах',
            'action': 'Выполнение команды: /system package print'
        })
        
        stdin, stdout, stderr = client.exec_command('/system package print')
        package_output = stdout.read().decode('utf-8', errors='ignore')
        package_error = stderr.read().decode('utf-8', errors='ignore')
        
        debug_info['steps'].append({
            'step': 'Информация о пакетах',
            'result': 'Успешно' if not package_error else 'Ошибка',
            'output': package_output,
            'error': package_error
        })
        
        client.close()
        
        # Логируем успешную отладку
        log = DeviceLog(
            device_id=device.id,
            action='device_debug_success',
            result=json.dumps({'status': 'success'}),
            details=f"Успешная отладка подключения к устройству {device.name}",
            performed_by=current_user.id
        )
        db.session.add(log)
        db.session.commit()
        
        return render_template('debug_connection.html', 
                             debug_info=debug_info,
                             device=device)
        
    except paramiko.AuthenticationException as e:
        debug_info['steps'].append({
            'step': 'Подключение',
            'result': 'Ошибка аутентификации',
            'details': str(e)
        })
    except paramiko.SSHException as e:
        debug_info['steps'].append({
            'step': 'Подключение',
            'result': 'Ошибка SSH',
            'details': str(e)
        })
    except Exception as e:
        debug_info['steps'].append({
            'step': 'Подключение',
            'result': 'Общая ошибка',
            'details': str(e),
            'traceback': traceback.format_exc()
        })
    
    # Логируем ошибку отладки
    log = DeviceLog(
        device_id=device.id,
        action='device_debug_failed',
        result=json.dumps({'status': 'error'}),
        details=f"Ошибка отладки подключения к устройству {device.name}",
        performed_by=current_user.id
    )
    db.session.add(log)
    db.session.commit()
    
    return render_template('debug_connection.html', 
                         debug_info=debug_info,
                         device=device)

# ========== ТЕМЫ ==========
@app.route('/toggle-theme', methods=['POST'])
def toggle_theme():
    """Переключение темы - только клиентская логика"""
    return jsonify({'success': True})

@app.context_processor
def inject_theme():
    """Добавляет тему в контекст всех шаблонов"""
    return {'current_theme': 'light'}

# ========== ОБРАБОТЧИКИ ОШИБОК ==========
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
@app.context_processor
def utility_processor():
    """Добавляет вспомогательные функции в контекст шаблонов"""
    def from_json(json_str):
        """Парсит JSON строку в объект Python"""
        try:
            return json.loads(json_str)
        except:
            return {}
    return dict(from_json=from_json)

@app.context_processor
def utility_processor():
    """Добавляет вспомогательные функции в контекст шаблонов"""
    def from_json(json_str):
        """Парсит JSON строку в объект Python"""
        try:
            if json_str:
                return json.loads(json_str)
        except:
            pass
        return {}
    
    return dict(from_json=from_json)