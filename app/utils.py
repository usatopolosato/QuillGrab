# app/utils.py

import os
import json
import uuid
import zipfile
import shutil
import logging
import threading
from datetime import datetime

import fitz  # PyMuPDF
import cv2
import numpy as np
from PIL import Image
from natsort import natsorted
from flask import current_app

logger = logging.getLogger(__name__)

PROJECTS_FILE = 'projects.json'
MAX_WIDTH = 1200
MAX_HEIGHT = 1600


def _safe_load_json(filepath, default=None):
    if not os.path.exists(filepath):
        return default
    encodings = ['utf-8', 'cp1251', 'latin-1']
    for enc in encodings:
        try:
            with open(filepath, 'r', encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
            return json.loads(content)
    except json.JSONDecodeError:
        return default


def _get_projects_file_path():
    storage = current_app.config['STORAGE_PATH']
    os.makedirs(storage, exist_ok=True)
    return os.path.join(storage, PROJECTS_FILE)


def load_projects():
    path = _get_projects_file_path()
    data = _safe_load_json(path)
    return data if data is not None else {}


def save_projects(projects):
    path = _get_projects_file_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(projects, f, indent=2, ensure_ascii=False)


def resize_image_to_fit(image_path, max_width=MAX_WIDTH, max_height=MAX_HEIGHT):
    try:
        img = Image.open(image_path)
        w, h = img.size
        scale = min(max_width / w, max_height / h, 1.0)
        if scale < 1.0:
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.LANCZOS)
            img.save(image_path)
    except Exception as e:
        logger.error(f"Ошибка при масштабировании {image_path}: {e}")


def _save_uploaded_image(file_storage, dest_dir):
    original_name = file_storage.filename
    if not original_name:
        return None
    base, ext = os.path.splitext(original_name)
    allowed_ext = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.bmp'}
    ext = ext.lower()
    if ext not in allowed_ext:
        ext = '.png'
    dest = os.path.join(dest_dir, f"{base}{ext}")
    counter = 1
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, f"{base}_{counter}{ext}")
        counter += 1
    file_storage.save(dest)
    return os.path.basename(dest)


def create_project_from_zip(name, zip_data):
    storage = current_app.config['STORAGE_PATH']
    project_id = str(uuid.uuid4())
    project_dir = os.path.join(storage, project_id)
    originals_dir = os.path.join(project_dir, 'original')
    tmp_zip = os.path.join(project_dir, 'archive.zip')
    try:
        os.makedirs(originals_dir)
        with open(tmp_zip, 'wb') as f:
            f.write(zip_data)
        image_names = []
        allowed_ext = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.tif', '.bmp'}
        with zipfile.ZipFile(tmp_zip, 'r') as zf:
            for member in zf.namelist():
                base = os.path.basename(member)
                if base.startswith('.') or base.startswith('__MACOSX'):
                    continue
                ext = os.path.splitext(member)[1].lower()
                if ext not in allowed_ext:
                    continue
                dest = os.path.join(originals_dir, base)
                counter = 1
                while os.path.exists(dest):
                    name_part, ext_part = os.path.splitext(base)
                    dest = os.path.join(originals_dir, f"{name_part}_{counter}{ext_part}")
                    counter += 1
                with zf.open(member) as source, open(dest, 'wb') as target:
                    shutil.copyfileobj(source, target)
                resize_image_to_fit(dest)
                image_names.append(os.path.basename(dest))
        os.remove(tmp_zip)
        if not image_names:
            raise ValueError("В архиве нет поддерживаемых изображений")
        image_names = natsorted(image_names)
        pages_count = len(image_names)
        projects = load_projects()
        projects[project_id] = {
            'name': name,
            'created': datetime.now().isoformat(),
            'pages': pages_count,
            'path': project_dir,
            'images': image_names
        }
        save_projects(projects)
        return project_id, pages_count
    except Exception:
        if os.path.exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
        raise


def create_project_from_pdf(name, pdf_data):
    storage = current_app.config['STORAGE_PATH']
    project_id = str(uuid.uuid4())
    project_dir = os.path.join(storage, project_id)
    originals_dir = os.path.join(project_dir, 'original')
    os.makedirs(originals_dir, exist_ok=True)
    try:
        pdf_doc = fitz.open(stream=pdf_data, filetype="pdf")
        image_names = []
        for page_num in range(len(pdf_doc)):
            page = pdf_doc.load_page(page_num)
            pix = page.get_pixmap(dpi=200)
            img_filename = f"page_{page_num + 1:03d}.png"
            img_path = os.path.join(originals_dir, img_filename)
            pix.save(img_path)
            resize_image_to_fit(img_path)
            image_names.append(img_filename)
        if not image_names:
            raise ValueError("Не удалось извлечь страницы из PDF")
        pdf_doc.close()
        pages_count = len(image_names)
        projects = load_projects()
        projects[project_id] = {
            'name': name,
            'created': datetime.now().isoformat(),
            'pages': pages_count,
            'path': project_dir,
            'images': image_names
        }
        save_projects(projects)
        return project_id, pages_count
    except Exception:
        if os.path.exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
        raise


def create_project_from_images(name, image_files):
    storage = current_app.config['STORAGE_PATH']
    project_id = str(uuid.uuid4())
    project_dir = os.path.join(storage, project_id)
    originals_dir = os.path.join(project_dir, 'original')
    os.makedirs(originals_dir, exist_ok=True)
    try:
        image_names = []
        for file in image_files:
            saved_name = _save_uploaded_image(file, originals_dir)
            if saved_name:
                saved_path = os.path.join(originals_dir, saved_name)
                resize_image_to_fit(saved_path)
                image_names.append(saved_name)
        if not image_names:
            raise ValueError("Нет допустимых изображений")
        image_names = natsorted(image_names)
        pages_count = len(image_names)
        projects = load_projects()
        projects[project_id] = {
            'name': name,
            'created': datetime.now().isoformat(),
            'pages': pages_count,
            'path': project_dir,
            'images': image_names
        }
        save_projects(projects)
        return project_id, pages_count
    except Exception:
        if os.path.exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
        raise


def get_project(project_id):
    return load_projects().get(project_id)


def delete_project(project_id):
    project = get_project(project_id)
    if not project:
        return False
    shutil.rmtree(project['path'], ignore_errors=True)
    projects = load_projects()
    del projects[project_id]
    save_projects(projects)
    return True


def list_projects():
    projects = load_projects()
    result = [
        {'id': pid, 'name': info['name'], 'created': info['created'], 'pages': info['pages']}
        for pid, info in projects.items()
    ]
    result.sort(key=lambda x: x['created'], reverse=True)
    return result


def detect_page(project_id, page_num, detector):
    proj = get_project(project_id)
    if not proj:
        raise FileNotFoundError("Проект не найден")
    if page_num < 1 or page_num > proj['pages']:
        raise ValueError("Неверный номер страницы")
    image_name = proj['images'][page_num - 1]
    originals_dir = os.path.join(proj['path'], 'original')
    image_path = os.path.join(originals_dir, image_name)
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Изображение {image_name} не найдено")

    pil_img = Image.open(image_path)
    w, h = pil_img.size
    scale = min(MAX_WIDTH / w, MAX_HEIGHT / h, 1.0)
    if scale < 1.0:
        new_size = (int(w * scale), int(h * scale))
        pil_img = pil_img.resize(new_size, Image.LANCZOS)
        temp_path = os.path.join(originals_dir, f"_temp_{image_name}")
        pil_img.save(temp_path)
        logger.info(f"[detect] Изображение сжато до {new_size}")
        raw_boxes = detector.predict(temp_path)
        os.remove(temp_path)
    else:
        raw_boxes = detector.predict(image_path)

    logger.info(f"[detect] Найдено объектов: {len(raw_boxes)}")
    detections = []
    for idx, box in enumerate(raw_boxes):
        x1, y1, x2, y2 = box['x1'], box['y1'], box['x2'], box['y2']
        detections.append({
            'id': f"box_{idx}",
            'class': box['class'],
            'x': round(x1),
            'y': round(y1),
            'width': round(x2 - x1),
            'height': round(y2 - y1),
            'confidence': round(box['conf'], 3)
        })
    pages_dir = os.path.join(proj['path'], 'pages', str(page_num))
    os.makedirs(pages_dir, exist_ok=True)
    json_path = os.path.join(pages_dir, 'detection_raw.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(detections, f, indent=2, ensure_ascii=False)
    logger.info(f"[detect] Результат сохранён в {json_path}")
    return detections


def get_detections(project_id, page_num, edited=False):
    proj = get_project(project_id)
    if not proj:
        return None
    pages_dir = os.path.join(proj['path'], 'pages', str(page_num))
    filename = 'detection_edited.json' if edited else 'detection_raw.json'
    json_path = os.path.join(pages_dir, filename)
    return _safe_load_json(json_path)


def save_detections(project_id, page_num, detections, edited=True):
    proj = get_project(project_id)
    if not proj:
        raise FileNotFoundError("Проект не найден")
    pages_dir = os.path.join(proj['path'], 'pages', str(page_num))
    os.makedirs(pages_dir, exist_ok=True)
    json_path = os.path.join(pages_dir, 'detection_edited.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(detections, f, indent=2, ensure_ascii=False)


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def deskew_line(image: Image.Image, bbox: dict) -> Image.Image:
    cropped = image.crop((bbox['x'], bbox['y'],
                          bbox['x'] + bbox['width'],
                          bbox['y'] + bbox['height']))
    gray = cv2.cvtColor(np.array(cropped), cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) == 0:
        return cropped
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5:
        return cropped
    (h, w) = cropped.size
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    rotated = cv2.warpAffine(np.array(cropped), M, (w, h),
                             flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(rotated)


def export_training_data(project_id):
    proj = get_project(project_id)
    if not proj:
        raise ValueError("Проект не найден")
    storage = current_app.config['STORAGE_PATH']
    train_path = current_app.config.get('TRAINING_DATA_PATH', os.path.join(storage, 'training_data'))
    detection_train_dir = os.path.join(train_path, 'detection')
    ocr_train_dir = os.path.join(train_path, 'ocr')
    os.makedirs(detection_train_dir, exist_ok=True)
    os.makedirs(ocr_train_dir, exist_ok=True)
    images_detection_dir = os.path.join(detection_train_dir, 'images')
    images_ocr_dir = os.path.join(ocr_train_dir, 'images')
    os.makedirs(images_detection_dir, exist_ok=True)
    os.makedirs(images_ocr_dir, exist_ok=True)
    detection_json_path = os.path.join(detection_train_dir, 'dataset.json')
    ocr_json_path = os.path.join(ocr_train_dir, 'dataset.json')

    detection_data = _safe_load_json(detection_json_path, default=[]) or []
    ocr_data = _safe_load_json(ocr_json_path, default=[]) or []
    detection_image_to_idx = {entry['image']: idx for idx, entry in enumerate(detection_data)}
    ocr_image_to_idx = {entry['image']: idx for idx, entry in enumerate(ocr_data)}
    stats = {'detection_pages': 0, 'ocr_lines': 0}

    for page_num in range(1, proj['pages'] + 1):
        page_dir = os.path.join(proj['path'], 'pages', str(page_num))
        if not os.path.exists(page_dir):
            continue

        # Detection
        detection_edited_path = os.path.join(page_dir, 'detection_edited.json')
        detection_raw_path = os.path.join(page_dir, 'detection_raw.json')
        detections = None
        if os.path.exists(detection_edited_path):
            detections = _safe_load_json(detection_edited_path)
        if detections is None and os.path.exists(detection_raw_path):
            detections = _safe_load_json(detection_raw_path)
        if detections and isinstance(detections, list) and len(detections) > 0:
            img_filename = proj['images'][page_num - 1]
            src_img_path = os.path.join(proj['path'], 'original', img_filename)
            if os.path.exists(src_img_path):
                ext = os.path.splitext(img_filename)[1]
                dest_img_name = f"{project_id}_page_{page_num}{ext}"
                dest_img_path = os.path.join(images_detection_dir, dest_img_name)
                shutil.copy2(src_img_path, dest_img_path)
                abs_path = os.path.abspath(dest_img_path)
                if abs_path in detection_image_to_idx:
                    detection_data[detection_image_to_idx[abs_path]]['annotations'] = detections
                else:
                    detection_data.append({'image': abs_path, 'annotations': detections})
                    detection_image_to_idx[abs_path] = len(detection_data) - 1
                stats['detection_pages'] += 1

        # OCR
        ocr_edited_path = os.path.join(page_dir, 'ocr_edited.json')
        ocr_raw_path = os.path.join(page_dir, 'ocr_raw.json')
        ocr_data_page = None
        if os.path.exists(ocr_edited_path):
            ocr_data_page = _safe_load_json(ocr_edited_path)
        if ocr_data_page is None and os.path.exists(ocr_raw_path):
            ocr_data_page = _safe_load_json(ocr_raw_path)
        if ocr_data_page and isinstance(ocr_data_page, dict) and 'boxes' in ocr_data_page:
            for box in ocr_data_page['boxes']:
                box_id = box.get('box_id')
                if not box_id:
                    continue
                for line in box.get('lines', []):
                    line_id = line.get('line_id')
                    if not line_id:
                        continue
                    text = line.get('edited_text') or line.get('text')
                    if not text or text == "[нет текста]":
                        continue
                    line_crop_path = os.path.join(page_dir, 'lines', box_id, f"{line_id}.png")
                    if not os.path.exists(line_crop_path):
                        continue
                    dest_crop_name = f"{project_id}_page_{page_num}_box_{box_id}_line_{line_id}.png"
                    dest_crop_path = os.path.join(images_ocr_dir, dest_crop_name)
                    shutil.copy2(line_crop_path, dest_crop_path)
                    abs_crop_path = os.path.abspath(dest_crop_path)
                    if abs_crop_path in ocr_image_to_idx:
                        ocr_data[ocr_image_to_idx[abs_crop_path]]['text'] = text
                    else:
                        ocr_data.append({'image': abs_crop_path, 'text': text})
                        ocr_image_to_idx[abs_crop_path] = len(ocr_data) - 1
                    stats['ocr_lines'] += 1

    with open(detection_json_path, 'w', encoding='utf-8') as f:
        json.dump(detection_data, f, ensure_ascii=False, indent=2)
    with open(ocr_json_path, 'w', encoding='utf-8') as f:
        json.dump(ocr_data, f, ensure_ascii=False, indent=2)
    return stats


def run_ocr_sync(project_id, page_num, manager, proj):
    page_dir = os.path.join(proj['path'], 'pages', str(page_num))
    ensure_dir(page_dir)

    detections = get_detections(project_id, page_num, edited=True)
    if detections is None:
        detections = get_detections(project_id, page_num, edited=False)
    if detections is None:
        detections = detect_page(project_id, page_num, manager.detector)

    text_boxes = [box for box in detections if box['class'] in ('text', 'title')]
    if not text_boxes:
        raise ValueError("На странице нет текстовых блоков для OCR")

    original_image_name = proj['images'][page_num - 1]
    original_image_path = os.path.join(proj['path'], 'original', original_image_name)
    full_img = Image.open(original_image_path)
    ocr_results = {'boxes': []}

    for box in text_boxes:
        box_bbox = {'x': box['x'], 'y': box['y'], 'width': box['width'], 'height': box['height']}
        try:
            box_crop = full_img.crop((box['x'], box['y'],
                                      box['x'] + box['width'],
                                      box['y'] + box['height']))
        except Exception as e:
            logger.error(f"Не удалось вырезать блок {box['id']}: {e}")
            continue

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
            try:
                line_img = box_crop.crop((lb['x'], lb['y'],
                                          lb['x'] + lb['width'],
                                          lb['y'] + lb['height']))
                lines_dir = os.path.join(page_dir, 'lines', box['id'])
                ensure_dir(lines_dir)
                line_filename = f"{line_id}.png"
                line_path = os.path.join(lines_dir, line_filename)
                line_img.save(line_path)
                crop_url = f"/api/projects/{project_id}/pages/{page_num}/lines/{box['id']}/{line_filename}"
            except Exception as e:
                logger.error(f"Не удалось сохранить кроп строки {line_id}: {e}")
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

    if not ocr_results['boxes']:
        raise ValueError("Не получено ни одной строки при OCR")

    import copy
    edited_data = copy.deepcopy(ocr_results)
    for box in edited_data['boxes']:
        lines = box['lines']
        symspell_texts = []
        for line in lines:
            raw = line['text']
            if raw and raw != "[нет текста]":
                corrected = manager.spell_checker.correct_text(raw)
                symspell_texts.append(corrected)
            else:
                symspell_texts.append(raw if raw else '')
        final_texts = manager.llm.correct_block_lines(symspell_texts)
        for line, final_text in zip(lines, final_texts):
            line['edited_text'] = final_text
        all_edited = [line.get('edited_text') or line.get('text') or '' for line in lines]
        box['combined_text'] = ' '.join(all_edited) if all_edited else '[нет текста]'

    ocr_raw_path = os.path.join(page_dir, 'ocr_raw.json')
    ocr_edited_path = os.path.join(page_dir, 'ocr_edited.json')
    with open(ocr_raw_path, 'w', encoding='utf-8') as f:
        json.dump(ocr_results, f, ensure_ascii=False, indent=2)
    with open(ocr_edited_path, 'w', encoding='utf-8') as f:
        json.dump(edited_data, f, ensure_ascii=False, indent=2)

    status_path = os.path.join(page_dir, 'ocr_status.json')
    with open(status_path, 'w', encoding='utf-8') as f:
        json.dump({'status': 'done',
                   'boxes_done': len(text_boxes),
                   'total_boxes': len(text_boxes)}, f)


def save_status(status_path, status):
    with open(status_path, 'w', encoding='utf-8') as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------
# ОЦИФРОВКА ПРОЕКТА (с передачей app)
# --------------------------------------------------------------
def digitize_project(project_id, manager, app):
    proj = get_project(project_id)
    if not proj:
        raise ValueError("Проект не найден")

    status_path = os.path.join(proj['path'], 'digitize_status.json')
    status = {
        'status': 'processing',
        'total_pages': proj['pages'],
        'processed_pages': 0,
        'pages': [{'page': i, 'status': 'pending', 'message': ''} for i in range(1, proj['pages'] + 1)]
    }
    save_status(status_path, status)
    logger.info(f"Digitization started for project {project_id}")

    def process():
        try:
            with app.app_context():
                if not manager:
                    logger.error("Model manager not available")
                    status['status'] = 'error'
                    status['message'] = 'Модели не загружены'
                    save_status(status_path, status)
                    return

                for page_num in range(1, proj['pages'] + 1):
                    page_status = status['pages'][page_num - 1]
                    page_dir = os.path.join(proj['path'], 'pages', str(page_num))

                    detection_exists = os.path.exists(os.path.join(page_dir, 'detection_raw.json'))
                    ocr_exists = os.path.exists(os.path.join(page_dir, 'ocr_raw.json'))

                    if detection_exists and ocr_exists:
                        page_status['status'] = 'skipped'
                        page_status['message'] = 'Уже оцифровано'
                        status['processed_pages'] += 1
                        save_status(status_path, status)
                        continue

                    # Детекция
                    if not detection_exists:
                        page_status['status'] = 'detecting'
                        save_status(status_path, status)
                        try:
                            detections = detect_page(project_id, page_num, manager.detector)
                            page_status['message'] = f'Найдено {len(detections)} блоков'
                        except Exception as e:
                            logger.exception(f"Detection failed for page {page_num}")
                            page_status['status'] = 'error'
                            page_status['message'] = f'Ошибка детекции: {str(e)}'
                            status['processed_pages'] += 1
                            save_status(status_path, status)
                            continue
                    else:
                        detections = get_detections(project_id, page_num, edited=False)
                        if detections is None:
                            detections = get_detections(project_id, page_num, edited=True)
                        if not detections:
                            page_status['status'] = 'error'
                            page_status['message'] = 'Детекция есть, но не загружается'
                            status['processed_pages'] += 1
                            save_status(status_path, status)
                            continue
                    # OCR, если отсутствует
                    if not ocr_exists:
                        page_status['status'] = 'ocr'
                        save_status(status_path, status)
                        try:
                            run_ocr_sync(project_id, page_num, manager, proj)
                            page_status['status'] = 'done'
                            page_status['message'] = 'OCR завершён'
                        except Exception as e:
                            logger.exception(f"OCR failed for page {page_num}")
                            page_status['status'] = 'error'
                            if str(e) == 'На странице нет текстовых блоков для OCR':
                                page_status['status'] = 'done'
                                page_status['message'] = 'Нет текстовых блоков, OCR не требуется'
                                continue

                            page_status['message'] = f'Ошибка OCR: {str(e)}'
                            status['processed_pages'] += 1
                            save_status(status_path, status)
                            continue
                    else:
                        page_status['status'] = 'done'
                        page_status['message'] = 'Уже оцифровано (OCR exists)'

                    if page_status['status'] != 'skipped':
                        status['processed_pages'] += 1
                    save_status(status_path, status)

                all_done = all(p['status'] in ('done', 'skipped') for p in status['pages'])
                status['status'] = 'done'
                status['message'] = 'Оцифровка завершена' if all_done else 'Оцифровка завершена с ошибками'
                save_status(status_path, status)
        except Exception as e:
            logger.exception(f"Fatal error in digitization process for project {project_id}")
            status['status'] = 'error'
            status['message'] = f'Критическая ошибка: {str(e)}'
            save_status(status_path, status)

    threading.Thread(target=process, name=f"Digitize-{project_id}").start()
    return status_path


def export_all_training_data_as_zip():
    """
    Собирает все тренировочные данные из папки training_data в ZIP-архив.
    Возвращает путь к созданному ZIP-файлу.
    """
    storage = current_app.config['STORAGE_PATH']
    train_path = current_app.config.get('TRAINING_DATA_PATH',
                                        os.path.join(storage, 'training_data'))

    if not os.path.exists(train_path):
        raise ValueError("Нет тренировочных данных")

    # Создаём временный ZIP-файл
    import tempfile
    zip_path = os.path.join(tempfile.gettempdir(),
                            f"training_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(train_path):
            for file in files:
                file_path = os.path.join(root, file)
                # Сохраняем относительный путь внутри архива
                arcname = os.path.relpath(file_path, os.path.dirname(train_path))
                zf.write(file_path, arcname)

    return zip_path


def generate_project_pdf(project_id):
    """
    Генерирует PDF-документ, восстанавливая страницу по данным детекции и OCR.
    Без использования фонового изображения.
    - Текстовые блоки (text, title) отображаются текстом из OCR (combined_text) с подходящим размером.
    - Блоки-изображения (image) вырезаются из оригинала и вставляются.
    - Координаты и размеры масштабируются под формат A4.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    proj = get_project(project_id)
    if not proj:
        raise ValueError("Проект не найден")

    # Регистрируем шрифт для кириллицы
    try:
        font_path = "C:/Windows/Fonts/arial.ttf"
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont('CustomFont', font_path))
            font_name = 'CustomFont'
        else:
            font_name = 'Helvetica'
    except:
        font_name = 'Helvetica'

    pdf_path = os.path.join(proj['path'], f"{proj['name']}.pdf")
    c = canvas.Canvas(pdf_path, pagesize=A4)
    page_width, page_height = A4

    margin = 10 * mm
    usable_width = page_width - 2 * margin
    usable_height = page_height - 2 * margin

    for page_num in range(1, proj['pages'] + 1):
        image_name = proj['images'][page_num - 1]
        image_path = os.path.join(proj['path'], 'original', image_name)
        if not os.path.exists(image_path):
            c.showPage()
            continue

        img = Image.open(image_path)
        img_width, img_height = img.size

        scale = min(usable_width / img_width, usable_height / img_height)
        offset_x = (page_width - img_width * scale) / 2
        offset_y = (page_height - img_height * scale) / 2

        page_dir = os.path.join(proj['path'], 'pages', str(page_num))

        # Загружаем детекции
        detections = None
        detection_edited_path = os.path.join(page_dir, 'detection_edited.json')
        detection_raw_path = os.path.join(page_dir, 'detection_raw.json')
        if os.path.exists(detection_edited_path):
            with open(detection_edited_path, 'r', encoding='utf-8') as f:
                detections = json.load(f)
        elif os.path.exists(detection_raw_path):
            with open(detection_raw_path, 'r', encoding='utf-8') as f:
                detections = json.load(f)

        if not detections:
            c.showPage()
            continue

        # Загружаем OCR
        ocr_data = None
        ocr_edited_path = os.path.join(page_dir, 'ocr_edited.json')
        ocr_raw_path = os.path.join(page_dir, 'ocr_raw.json')
        if os.path.exists(ocr_edited_path):
            with open(ocr_edited_path, 'r', encoding='utf-8') as f:
                ocr_data = json.load(f)
        elif os.path.exists(ocr_raw_path):
            with open(ocr_raw_path, 'r', encoding='utf-8') as f:
                ocr_data = json.load(f)

        ocr_dict = {}
        if ocr_data and 'boxes' in ocr_data:
            for box in ocr_data['boxes']:
                ocr_dict[box['box_id']] = box

        # Рисуем изображения
        image_blocks = [d for d in detections if d.get('class') == 'image']
        text_blocks = [d for d in detections if d.get('class') in ('text', 'title')]

        for detection in image_blocks:
            box_id = detection.get('id')
            x = detection.get('x', 0)
            y = detection.get('y', 0)
            w = detection.get('width', 0)
            h = detection.get('height', 0)

            scaled_x = offset_x + x * scale
            scaled_y = offset_y + (img_height - y - h) * scale
            scaled_w = w * scale
            scaled_h = h * scale

            if scaled_w < 2 or scaled_h < 2:
                continue

            try:
                crop = img.crop((x, y, x + w, y + h))
                temp_crop_path = os.path.join(page_dir, f"_temp_crop_{box_id}.png")
                crop.save(temp_crop_path)
                img_reader = ImageReader(temp_crop_path)
                c.drawImage(img_reader, scaled_x, scaled_y, scaled_w, scaled_h)
                try:
                    os.remove(temp_crop_path)
                except:
                    pass
            except Exception as e:
                logger.error(f"Не удалось обработать image-блок {box_id}: {e}")
                continue

        # Рисуем текстовые блоки
        for detection in text_blocks:
            box_id = detection.get('id')
            x = detection.get('x', 0)
            y = detection.get('y', 0)
            w = detection.get('width', 0)
            h = detection.get('height', 0)
            box_class = detection.get('class', 'unknown')

            scaled_x = offset_x + x * scale
            scaled_y = offset_y + (img_height - y - h) * scale
            scaled_w = w * scale
            scaled_h = h * scale

            if scaled_w < 5 or scaled_h < 5:
                continue

            # Ищем текст
            text = None
            if box_id in ocr_dict:
                text = ocr_dict[box_id].get('combined_text', '')
            else:
                for ocr_box in ocr_data.get('boxes', []):
                    bbox = ocr_box.get('bbox', {})
                    if bbox:
                        ox = bbox.get('x', 0)
                        oy = bbox.get('y', 0)
                        ow = bbox.get('width', 0)
                        oh = bbox.get('height', 0)
                        if (x < ox + ow and x + w > ox and
                            y < oy + oh and y + h > oy):
                            text = ocr_box.get('combined_text', '')
                            break

            if text and text != "[нет текста]":
                # Размер шрифта как в отладочной версии
                if box_class == 'title':
                    font_size = 12
                    c.setFillColorRGB(0.2, 0.2, 0.8)  # синий для заголовков
                else:
                    font_size = 8
                    c.setFillColorRGB(0, 0, 0)  # чёрный для текста

                c.setFont(font_name, font_size)

                # Выводим весь текст без обрезания
                text_y = scaled_y + scaled_h - 2
                c.drawString(scaled_x + 2, text_y, text)

            else:
                # Если текста нет, рисуем пунктирную рамку
                c.setStrokeColorRGB(0.8, 0.8, 0.8)
                c.setDash(2, 2)
                c.rect(scaled_x, scaled_y, scaled_w, scaled_h)
                c.setDash()
                c.setFont('Helvetica', 6)
                c.setFillColorRGB(0.5, 0.5, 0.5)
                c.drawString(scaled_x + 2, scaled_y + 2, f"[{box_class}]")

        c.showPage()

    c.save()
    logger.info(f"PDF сгенерирован для проекта {project_id}: {pdf_path}")
    return pdf_path


def generate_project_docx(project_id):
    """
    Генерирует DOCX из PDF проекта с помощью pdf2docx.
    """
    try:
        from pdf2docx import Converter
    except ImportError:
        raise ImportError("Библиотека pdf2docx не установлена. Установите: pip install pdf2docx")

    proj = get_project(project_id)
    if not proj:
        raise ValueError("Проект не найден")

    # Сначала генерируем PDF
    pdf_path = generate_project_pdf(project_id)

    # Путь для DOCX
    docx_path = os.path.join(proj['path'], f"{proj['name']}.docx")

    # Конвертируем PDF в DOCX
    cv = Converter(pdf_path)
    cv.convert(docx_path, start=0, end=None)
    cv.close()

    logger.info(f"DOCX generated for project {project_id}: {docx_path}")
    return docx_path
