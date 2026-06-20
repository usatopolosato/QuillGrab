import os
import io
import json
import uuid
import threading
import logging
import copy
from flask import (Blueprint, render_template, request, redirect,
                   url_for, current_app, send_from_directory, send_file, jsonify)
from PIL import Image
from app.utils import (create_project, get_project, delete_project, list_projects,
                       detect_page, get_detections, save_detections,
                       ensure_dir)

main = Blueprint('main', __name__)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------
# Страницы
# --------------------------------------------------------------
@main.route('/')
def index():
    projects = list_projects()
    manager = current_app.config.get('MODEL_MANAGER')
    models_status = "Модели загружены" if manager else "Модели не загружены"
    return render_template('index.html', projects=projects, models_status=models_status)


@main.route('/create', methods=['GET', 'POST'])
def create():
    if request.method == 'POST':
        if 'archive' not in request.files:
            return "Нет файла", 400
        file = request.files['archive']
        if file.filename == '':
            return "Файл не выбран", 400
        if not file.filename.lower().endswith('.zip'):
            return "Только ZIP-архивы", 400

        zip_bytes = file.read()
        if len(zip_bytes) > current_app.config['MAX_CONTENT_LENGTH']:
            return "Файл слишком большой", 400

        project_name = request.form.get('name', '').strip()
        if not project_name:
            project_name = os.path.splitext(file.filename)[0]

        try:
            project_id, pages = create_project(project_name, zip_bytes)
        except ValueError as e:
            return str(e), 400

        return redirect(url_for('main.project', project_id=project_id))

    return render_template('create.html')


@main.route('/project/<project_id>')
def project(project_id):
    proj = get_project(project_id)
    if not proj:
        return "Проект не найден", 404
    pages = list(range(1, proj['pages'] + 1))
    return render_template('project.html', project=proj, project_id=project_id, pages=pages)


@main.route('/project/<project_id>/delete', methods=['POST'])
def delete_project_web(project_id):
    delete_project(project_id)
    return redirect(url_for('main.index'))


@main.route('/annotate/<project_id>/<int:page>')
def annotate(project_id, page):
    proj = get_project(project_id)
    if not proj or page < 1 or page > proj['pages']:
        return "Страница не найдена", 404
    image_url = url_for('main.serve_page_image', project_id=project_id, page=page)
    return render_template('annotate.html',
                           project_id=project_id,
                           page=page,
                           total_pages=proj['pages'],
                           image_url=image_url)


@main.route('/ocr/<project_id>/<int:page>')
def ocr_page(project_id, page):
    proj = get_project(project_id)
    if not proj or page < 1 or page > proj['pages']:
        return "Страница не найдена", 404
    image_url = url_for('main.serve_page_image', project_id=project_id, page=page)
    return render_template('ocr.html',
                           project_id=project_id,
                           page=page,
                           total_pages=proj['pages'],
                           image_url=image_url)


# --------------------------------------------------------------
# API – Проекты
# --------------------------------------------------------------
@main.route('/api/projects', methods=['POST'])
def api_create_project():
    if 'archive' not in request.files:
        return jsonify({'error': 'Нет файла'}), 400
    file = request.files['archive']
    if file.filename == '' or not file.filename.lower().endswith('.zip'):
        return jsonify({'error': 'Только ZIP'}), 400

    zip_bytes = file.read()
    project_name = request.form.get('name', '').strip()
    if not project_name:
        project_name = os.path.splitext(file.filename)[0]

    try:
        project_id, pages = create_project(project_name, zip_bytes)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    return jsonify({'project_id': project_id, 'pages': pages}), 201


@main.route('/api/projects/<project_id>/pages/<int:page>/image')
def serve_page_image(project_id, page):
    proj = get_project(project_id)
    if not proj or page < 1 or page > proj['pages']:
        return "Not found", 404
    image_name = proj['images'][page - 1]
    originals_dir = os.path.join(proj['path'], 'original')
    return send_from_directory(originals_dir, image_name)


@main.route('/api/projects/<project_id>/pages/<int:page>/image_preview')
def serve_page_image_preview(project_id, page):
    """Отдаёт сжатое изображение (ширина по умолчанию 1200px)."""
    proj = get_project(project_id)
    if not proj or page < 1 or page > proj['pages']:
        return "Not found", 404
    image_name = proj['images'][page - 1]
    originals_dir = os.path.join(proj['path'], 'original')
    image_path = os.path.join(originals_dir, image_name)

    max_width = request.args.get('width', 1200, type=int)
    try:
        pil_img = Image.open(image_path).convert('RGB')
        w, h = pil_img.size
        if w > max_width:
            ratio = max_width / w
            new_size = (max_width, int(h * ratio))
            pil_img = pil_img.resize(new_size, Image.LANCZOS)
        buf = io.BytesIO()
        pil_img.save(buf, format='JPEG', quality=85)
        buf.seek(0)
        return send_file(buf, mimetype='image/jpeg')
    except Exception as e:
        current_app.logger.error(f"Preview error: {e}")
        return send_from_directory(originals_dir, image_name)


@main.route('/api/projects/<project_id>', methods=['DELETE'])
def api_delete_project(project_id):
    success = delete_project(project_id)
    if success:
        return jsonify({'status': 'deleted'}), 200
    return jsonify({'error': 'Project not found'}), 404


# --------------------------------------------------------------
# API – Детекция и разметка
# --------------------------------------------------------------
@main.route('/api/projects/<project_id>/pages/<int:page>/detect', methods=['POST'])
def api_detect(project_id, page):
    force = request.args.get('force', 'false').lower() == 'true'
    if not force:
        existing = get_detections(project_id, page, edited=False)
        if existing is not None:
            return jsonify({'detections': existing, 'cached': True})

    manager = current_app.config.get('MODEL_MANAGER')
    if not manager:
        return jsonify({'error': 'Модели не загружены'}), 500

    try:
        detections = detect_page(project_id, page, manager.detector)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    return jsonify({'detections': detections, 'cached': False})


@main.route('/api/projects/<project_id>/pages/<int:page>/detections', methods=['GET'])
def api_get_detections(project_id, page):
    edited = request.args.get('edited', 'false').lower() == 'true'
    detections = get_detections(project_id, page, edited=edited)
    if detections is None:
        return jsonify({'error': 'Разметка не найдена'}), 404
    return jsonify({'detections': detections})


@main.route('/api/projects/<project_id>/pages/<int:page>/detections', methods=['PUT'])
def api_save_detections(project_id, page):
    data = request.get_json()
    if not data or 'detections' not in data:
        return jsonify({'error': 'Некорректные данные'}), 400
    try:
        save_detections(project_id, page, data['detections'], edited=True)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    return jsonify({'status': 'saved'})


# --------------------------------------------------------------
# API – Детекция строк и распознавание (OCR)
# --------------------------------------------------------------
@main.route('/api/projects/<project_id>/pages/<int:page>/ocr', methods=['POST'])
def api_run_ocr(project_id, page):
    """Запускает OCR для страницы с помощью PaddleOCR (встроенная детекция строк)."""
    force = request.args.get('force', 'false').lower() == 'true'
    edited = request.args.get('edited', 'false').lower() == 'true'

    proj = get_project(project_id)
    if not proj or page < 1 or page > proj['pages']:
        return jsonify({'error': 'Project or page not found'}), 404

    manager = current_app.config.get('MODEL_MANAGER')
    if not manager:
        return jsonify({'error': 'Model manager not initialized'}), 500

    page_dir = os.path.join(proj['path'], 'pages', str(page))
    ensure_dir(page_dir)
    ocr_status_path = os.path.join(page_dir, 'ocr_status.json')
    ocr_raw_path = os.path.join(page_dir, 'ocr_raw.json')
    ocr_edited_path = os.path.join(page_dir, 'ocr_edited.json')

    # Если уже есть результат и не force – вернуть готовый
    if not force and os.path.exists(ocr_raw_path):
        with open(ocr_raw_path, 'r') as f:
            return jsonify(json.load(f))

    # Если уже обрабатывается – вернуть статус
    if os.path.exists(ocr_status_path):
        with open(ocr_status_path, 'r') as f:
            status = json.load(f)
        if status.get('status') == 'processing':
            return jsonify({'status': 'processing'}), 202

    # Загружаем (или создаём) разметку блоков
    detections = get_detections(project_id, page, edited=edited)
    if detections is None:
        logger.info("No detections found, running auto-detection...")
        try:
            detections = detect_page(project_id, page, manager.detector)
        except Exception as e:
            return jsonify({'error': 'Auto-detection failed: ' + str(e)}), 500

    # Проверяем, есть ли текст или заголовки
    text_boxes = [box for box in detections if box['class'] in ('text', 'title')]
    if not text_boxes:
        return jsonify({'error': 'На странице не найдено текстовых или заголовочных блоков. Распознавание невозможно.'}), 400

    original_image_name = proj['images'][page - 1]
    original_image_path = os.path.join(proj['path'], 'original', original_image_name)

    status = {'status': 'processing', 'boxes_done': 0, 'total_boxes': len(text_boxes)}
    with open(ocr_status_path, 'w') as f:
        json.dump(status, f)

    def process():
        nonlocal status
        try:
            full_img = Image.open(original_image_path)
            ocr_results = {'boxes': []}

            for i, box in enumerate(text_boxes):
                box_bbox = {
                    'x': box['x'],
                    'y': box['y'],
                    'width': box['width'],
                    'height': box['height']
                }

                # Вырезаем область блока
                try:
                    box_crop = full_img.crop((box['x'], box['y'],
                                              box['x'] + box['width'],
                                              box['y'] + box['height']))
                except Exception as e:
                    logger.error(f"Failed to crop box {box['id']}: {e}")
                    continue

                # Временный файл для PaddleOCR
                tmp_crop_path = os.path.join(page_dir, f"_tmp_{box['id']}.png")
                box_crop.save(tmp_crop_path)

                lines_info = manager.ocr.predict(tmp_crop_path)
                try:
                    os.remove(tmp_crop_path)
                except Exception:
                    pass

                lines_data = []
                combined_parts = []

                for line in lines_info:
                    line_id = str(uuid.uuid4())[:8]
                    lb = line['bbox']

                    # Вырезаем изображение строки
                    try:
                        line_img = box_crop.crop((lb['x'], lb['y'],
                                                  lb['x'] + lb['width'],
                                                  lb['y'] + lb['height']))
                        lines_dir = os.path.join(page_dir, 'lines', box['id'])
                        ensure_dir(lines_dir)
                        line_filename = f"{line_id}.png"
                        line_path = os.path.join(lines_dir, line_filename)
                        line_img.save(line_path)
                        crop_url = f"/api/projects/{project_id}/pages/{page}/lines/{box['id']}/{line_filename}"
                    except Exception as e:
                        logger.error(f"Failed to save line image {line_id}: {e}")
                        crop_url = ""

                    text = line['text']
                    conf = line['confidence']
                    if not text.strip():
                        text = "[нет текста]"

                    lines_data.append({
                        'line_id': line_id,
                        'bbox': lb,
                        'crop_url': crop_url,
                        'text': text,
                        'confidence': round(conf, 3)
                    })
                    combined_parts.append(text.strip())

                ocr_results['boxes'].append({
                    'box_id': box['id'],
                    'class': box['class'],
                    'bbox': box_bbox,
                    'lines': lines_data,
                    'combined_text': ' '.join(combined_parts) if combined_parts else "[нет текста]"
                })

                # Обновляем статус
                status['boxes_done'] = i + 1
                with open(ocr_status_path, 'w') as f:
                    json.dump(status, f)

            # ---------- ПОСТОБРАБОТКА ----------
            if not ocr_results['boxes']:
                status['status'] = 'error'
                status['message'] = 'После обработки не получено ни одной строки.'
            else:
                # Сохраняем raw
                with open(ocr_raw_path, 'w') as f:
                    json.dump(ocr_results, f, ensure_ascii=False, indent=2)

                # Создаём edited-версию
                edited_data = copy.deepcopy(ocr_results)

                for box in edited_data['boxes']:
                    lines = box['lines']

                    # 1. SymSpell для каждой строки
                    symspell_texts = []
                    for line in lines:
                        raw = line['text']
                        if raw and raw != "[нет текста]":
                            corrected = manager.spell_checker.correct_text(raw)
                            symspell_texts.append(corrected)
                        else:
                            symspell_texts.append(raw if raw else '')

                    # 2. LLM корректирует целый блок с нумерацией
                    final_texts = manager.llm.correct_block_lines(symspell_texts)

                    # 3. Записываем edited_text в строки
                    for line, final_text in zip(lines, final_texts):
                        line['edited_text'] = final_text

                    # 4. Обновляем combined_text на основе edited_text
                    all_edited = [line.get('edited_text') or line.get('text') or '' for line in lines]
                    box['combined_text'] = ' '.join(all_edited) if all_edited else '[нет текста]'

                # Сохраняем edited
                with open(ocr_edited_path, 'w') as f:
                    json.dump(edited_data, f, ensure_ascii=False, indent=2)

                status['status'] = 'done'

            with open(ocr_status_path, 'w') as f:
                json.dump(status, f)

        except Exception as e:
            logger.exception("OCR process fatal")
            status['status'] = 'error'
            status['message'] = str(e)
            with open(ocr_status_path, 'w') as f:
                json.dump(status, f)

    threading.Thread(target=process).start()
    return jsonify({'status': 'processing'}), 202


@main.route('/api/projects/<project_id>/pages/<int:page>/ocr/status')
def api_ocr_status(project_id, page):
    proj = get_project(project_id)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404
    status_path = os.path.join(proj['path'], 'pages', str(page), 'ocr_status.json')
    if not os.path.exists(status_path):
        return jsonify({'status': 'not_started'})
    with open(status_path) as f:
        return jsonify(json.load(f))


@main.route('/api/projects/<project_id>/pages/<int:page>/ocr', methods=['GET'])
def api_get_ocr(project_id, page):
    """Возвращает ocr_raw.json или ocr_edited.json"""
    edited = request.args.get('edited', 'false').lower() == 'true'
    proj = get_project(project_id)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404
    page_dir = os.path.join(proj['path'], 'pages', str(page))
    filename = 'ocr_edited.json' if edited else 'ocr_raw.json'
    filepath = os.path.join(page_dir, filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'No OCR data'}), 404
    with open(filepath) as f:
        return jsonify(json.load(f))


@main.route('/api/projects/<project_id>/pages/<int:page>/ocr', methods=['PUT'])
def api_save_ocr(project_id, page):
    """Сохраняет исправленные тексты и добавляет ручные строки."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid data'}), 400

    proj = get_project(project_id)
    if not proj:
        return jsonify({'error': 'Project not found'}), 404
    page_dir = os.path.join(proj['path'], 'pages', str(page))
    raw_path = os.path.join(page_dir, 'ocr_raw.json')
    edited_path = os.path.join(page_dir, 'ocr_edited.json')

    # Загружаем существующие данные (если есть)
    ocr_data = {'boxes': []}
    if os.path.exists(edited_path):
        with open(edited_path) as f:
            ocr_data = json.load(f)
    elif os.path.exists(raw_path):
        with open(raw_path) as f:
            ocr_data = json.load(f)

    # Обновляем строки из поля lines
    if 'lines' in data:
        edits_dict = {item['line_id']: item['edited_text'] for item in data['lines']}
        for box in ocr_data['boxes']:
            for line in box.get('lines', []):
                if line['line_id'] in edits_dict:
                    line['edited_text'] = edits_dict[line['line_id']]
            # Пересчитываем combined_text
            all_texts = [line.get('edited_text') or line.get('text') or '' for line in box.get('lines', [])]
            box['combined_text'] = ' '.join(all_texts) if all_texts else '[нет текста]'

    # Добавляем ручные строки в существующие блоки
    if 'manual_lines' in data:
        for ml in data['manual_lines']:
            box_id = ml.get('box_id')
            text = ml.get('text', '')
            for box in ocr_data['boxes']:
                if box['box_id'] == box_id:
                    new_line = {
                        'line_id': str(uuid.uuid4()),
                        'text': text,
                        'confidence': 1.0,
                        'crop_url': '',
                        'bbox': box.get('bbox'),
                        'edited_text': text
                    }
                    box.setdefault('lines', []).append(new_line)
                    all_texts = [l.get('edited_text') or l.get('text') or '' for l in box['lines']]
                    box['combined_text'] = ' '.join(all_texts)
                    break

    with open(edited_path, 'w') as f:
        json.dump(ocr_data, f, ensure_ascii=False, indent=2)

    # Сохраняем в training_data для строк, где текст изменился
    train_dir = os.path.join(current_app.config.get('TRAINING_DATA_PATH', 'training_data'), 'ocr')
    for box in ocr_data['boxes']:
        for line in box.get('lines', []):
            if 'edited_text' in line and line['edited_text'] != line.get('text', ''):
                line_crop_path = os.path.join(proj['path'], 'pages', str(page),
                                             'lines', box['box_id'], f"{line['line_id']}.png")
                if os.path.exists(line_crop_path):
                    dest_dir = os.path.join(train_dir, line['line_id'])
                    ensure_dir(dest_dir)
                    import shutil
                    shutil.copy2(line_crop_path, os.path.join(dest_dir, 'crop.png'))
                    with open(os.path.join(dest_dir, 'ground_truth.txt'), 'w', encoding='utf-8') as gt:
                        gt.write(line['edited_text'])
                    with open(os.path.join(dest_dir, 'raw.txt'), 'w', encoding='utf-8') as rw:
                        rw.write(line.get('text', ''))

    return jsonify({'status': 'saved'})


@main.route('/api/projects/<project_id>/pages/<int:page>/lines/<path:filename>')
def serve_line_image(project_id, page, filename):
    """Отдаёт изображение строки."""
    proj = get_project(project_id)
    if not proj:
        return "Not found", 404
    file_path = os.path.join(proj['path'], 'pages', str(page), 'lines', filename)
    if not os.path.exists(file_path):
        return "File not found", 404
    return send_file(file_path, mimetype='image/png')