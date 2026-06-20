// static/js/ocr.js

let canvas;
let ocrData = null;
let detectionBoxes = [];        // загруженные детекции
let ocrBoxes = [];
let manualMode = false;
let drawStart = null;
let tempRect = null;
let currentBoxForLine = null;

window.addEventListener('load', () => {
    canvas = new fabric.Canvas('c', {
        selection: false,
        preserveObjectStacking: true
    });

    fabric.Image.fromURL(IMAGE_URL, (img) => {
        canvas.setWidth(img.width);
        canvas.setHeight(img.height);
        canvas.setBackgroundImage(img, canvas.renderAll.bind(canvas));
        initPage();
    }, { crossOrigin: 'anonymous' });

    document.getElementById('btn-run-ocr').addEventListener('click', runOCR);
    document.getElementById('btn-save-ocr').addEventListener('click', saveOCR);

    canvas.on('mouse:down', onMouseDown);
    canvas.on('mouse:move', onMouseMove);
    canvas.on('mouse:up', onMouseUp);
});

// ---------- Инициализация страницы ----------
function initPage() {
    // Пытаемся загрузить готовый OCR
    fetch(API_OCR + '?edited=true')
        .then(res => {
            if (res.ok) return res.json();
            throw new Error('no ocr');
        })
        .then(data => {
            // OCR уже существует – отрисовываем его
            renderOCR(data);
            // После отрисовки OCR обязательно подгружаем детекции, чтобы кнопка работала
            return loadDetectionsForReuse();
        })
        .catch(() => {
            // OCR нет – загружаем детекции и показываем их
            loadDetectionsAndShow();
        });
}

// Загружает детекции, не рисуя их, только для наполнения detectionBoxes
async function loadDetectionsForReuse() {
    try {
        let res = await fetch(DETECTIONS_API + '?edited=true');
        if (res.ok) {
            let data = await res.json();
            detectionBoxes = data.detections || [];
            return;
        }
    } catch (e) { }
    try {
        let res = await fetch(DETECTIONS_API + '?edited=false');
        if (res.ok) {
            let data = await res.json();
            detectionBoxes = data.detections || [];
            return;
        }
    } catch (e) { }
    // Если совсем ничего нет – запускаем авто-детекцию (но без отрисовки, только сохраняем данные)
    try {
        let res = await fetch(`/api/projects/${PROJECT_ID}/pages/${PAGE}/detect`, { method: 'POST' });
        let data = await res.json();
        detectionBoxes = data.detections || [];
    } catch (e) {
        console.error('Failed to load detections for reuse', e);
    }
}

function loadDetectionsAndShow() {
    fetch(DETECTIONS_API + '?edited=true')
        .then(res => res.ok ? res.json() : Promise.reject())
        .then(data => {
            detectionBoxes = data.detections || [];
            drawDetectionBoxes(detectionBoxes);
        })
        .catch(() => {
            fetch(DETECTIONS_API + '?edited=false')
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    if (data && data.detections) {
                        detectionBoxes = data.detections;
                        drawDetectionBoxes(detectionBoxes);
                    } else {
                        // Совсем нет разметки – запускаем авто‑детекцию
                        runDetectionAndThenDraw();
                    }
                });
        });
}

function runDetectionAndThenDraw() {
    const btn = document.getElementById('btn-run-ocr');
    btn.disabled = true;
    btn.textContent = 'Поиск объектов...';
    fetch(`/api/projects/${PROJECT_ID}/pages/${PAGE}/detect`, { method: 'POST' })
        .then(res => res.json())
        .then(data => {
            detectionBoxes = data.detections || [];
            drawDetectionBoxes(detectionBoxes);
            btn.disabled = false;
            btn.textContent = 'Распознать текст';
        })
        .catch(err => {
            alert('Ошибка авто-детекции: ' + err);
            btn.disabled = false;
            btn.textContent = 'Распознать текст';
        });
}

// ---------- Отрисовка детекционных рамок ----------
function drawDetectionBoxes(boxes) {
    // Полностью очищаем canvas
    canvas.getObjects().forEach(obj => canvas.remove(obj));
    boxes.forEach(box => {
        const color = getColorByClass(box.class);
        const rect = new fabric.Rect({
            left: box.x, top: box.y,
            width: box.width, height: box.height,
            fill: 'rgba(0,0,0,0.05)',
            stroke: color,
            strokeWidth: 2,
            selectable: false,
            evented: false
        });
        const label = new fabric.Text(box.class, {
            left: box.x + 4, top: box.y + 4,
            fontSize: 12, fill: color,
            backgroundColor: 'rgba(255,255,255,0.8)',
            selectable: false, evented: false
        });
        canvas.add(rect);
        canvas.add(label);
    });
    canvas.renderAll();
}

function getColorByClass(cls) {
    switch (cls) {
        case 'text': return '#0066ff';
        case 'image': return '#00aa00';
        case 'title': return '#ff6600';
        default: return '#cccccc';
    }
}

// ---------- Ручное добавление строки (режим рисования) ----------
function enterDrawingModeForBox(boxId) {
    if (manualMode) return;
    currentBoxForLine = boxId;
    manualMode = true;
    canvas.selection = false;
    canvas.defaultCursor = 'crosshair';
}

function exitDrawingMode() {
    manualMode = false;
    currentBoxForLine = null;
    canvas.selection = true;
    canvas.defaultCursor = 'default';
}

function onMouseDown(opt) {
    if (!manualMode) return;
    const pointer = canvas.getPointer(opt.e);
    drawStart = { x: pointer.x, y: pointer.y };
    tempRect = new fabric.Rect({
        left: pointer.x, top: pointer.y,
        width: 0, height: 0,
        fill: 'rgba(255,0,0,0.2)', stroke: 'red',
        strokeDashArray: [5, 5],
        selectable: false, evented: false
    });
    canvas.add(tempRect);
}

function onMouseMove(opt) {
    if (!manualMode || !tempRect) return;
    const pointer = canvas.getPointer(opt.e);
    tempRect.set({
        left: Math.min(drawStart.x, pointer.x),
        top: Math.min(drawStart.y, pointer.y),
        width: Math.abs(pointer.x - drawStart.x),
        height: Math.abs(pointer.y - drawStart.y)
    });
    canvas.renderAll();
}

function onMouseUp() {
    if (!manualMode || !tempRect) return;
    const finalRect = {
        x: tempRect.left,
        y: tempRect.top,
        width: tempRect.width,
        height: tempRect.height
    };
    canvas.remove(tempRect);
    tempRect = null;
    const text = prompt('Введите текст для выделенной строки:');
    if (text !== null && text.trim() !== '') {
        addLineToBox(currentBoxForLine, text.trim(), finalRect);
    }
    exitDrawingMode();
}

function addLineToBox(boxId, text, bbox) {
    const card = document.querySelector(`.ocr-card[data-box-id="${boxId}"]`);
    if (!card) return;
    const linesContainer = card.querySelector('.lines-container');
    const boxIdx = parseInt(card.dataset.boxIdx);
    const newLineId = 'manual_line_' + Date.now();
    const lineHtml = `
        <div class="row mb-2 align-items-center line-row" data-line-id="${newLineId}">
            <div class="col-md-2"><img src="" class="img-thumbnail" style="max-height: 40px;"></div>
            <div class="col-md-8">
                <textarea class="form-control form-control-sm ocr-text" data-line-id="${newLineId}" data-box-idx="${boxIdx}" rows="2">${text}</textarea>
                <small class="text-muted">Вручную</small>
            </div>
            <div class="col-md-2 text-end">
                <button class="btn btn-sm btn-outline-danger delete-line-btn" data-line-id="${newLineId}" data-box-idx="${boxIdx}">✕</button>
            </div>
        </div>`;
    linesContainer.insertAdjacentHTML('beforeend', lineHtml);
    const newDelBtn = linesContainer.querySelector(`.delete-line-btn[data-line-id="${newLineId}"]`);
    newDelBtn.addEventListener('click', function() {
        const lineId = this.dataset.lineId;
        const boxIdx = this.dataset.boxIdx;
        const lineRow = document.querySelector(`.line-row[data-line-id="${lineId}"]`);
        if (lineRow) lineRow.remove();
        updateCombinedText(boxIdx);
    });
    updateCombinedText(boxIdx);
}

// ---------- Запуск OCR ----------
async function runOCR() {
    // Убеждаемся, что у нас есть актуальные детекции
    if (!detectionBoxes.length) {
        await loadDetectionsForReuse();
    }
    const textBoxes = detectionBoxes.filter(b => b.class === 'text' || b.class === 'title');
    if (textBoxes.length === 0) {
        alert('Нет текстовых блоков для распознавания');
        return;
    }
    const btn = document.getElementById('btn-run-ocr');
    btn.disabled = true;
    btn.textContent = 'Распознавание...';

    // Очищаем детекционные рамки перед запросом
    canvas.getObjects().forEach(obj => canvas.remove(obj));
    canvas.renderAll();

    fetch(API_OCR + '?force=true&edited=true', { method: 'POST' })
        .then(res => res.json().then(data => ({ status: res.status, body: data })))
        .then(({ status, body }) => {
            if (status === 202 && body.status === 'processing') {
                showProgress();
                pollStatus();
            } else if (body.error) {
                alert(body.error);
                btn.disabled = false;
                btn.textContent = 'Распознать текст';
                if (detectionBoxes.length) drawDetectionBoxes(detectionBoxes);
            } else {
                renderOCR(body);
                btn.disabled = false;
                btn.textContent = 'Распознать текст';
            }
        })
        .catch(err => {
            alert('Ошибка: ' + err);
            btn.disabled = false;
            btn.textContent = 'Распознать текст';
            if (detectionBoxes.length) drawDetectionBoxes(detectionBoxes);
        });
}

function showProgress() {
    document.getElementById('progress').classList.remove('d-none');
}
function hideProgress() {
    document.getElementById('progress').classList.add('d-none');
}
function updateProgress(percent) {
    const bar = document.querySelector('.progress-bar');
    bar.style.width = percent + '%';
    bar.textContent = percent + '%';
}

function pollStatus() {
    const interval = setInterval(() => {
        fetch(API_OCR_STATUS)
            .then(res => res.json())
            .then(status => {
                if (status.status === 'done') {
                    clearInterval(interval);
                    hideProgress();
                    document.getElementById('btn-run-ocr').disabled = false;
                    document.getElementById('btn-run-ocr').textContent = 'Распознать текст';
                    fetch(API_OCR + '?edited=false')
                        .then(res => res.json())
                        .then(data => renderOCR(data));
                } else if (status.status === 'error') {
                    clearInterval(interval);
                    hideProgress();
                    document.getElementById('btn-run-ocr').disabled = false;
                    document.getElementById('btn-run-ocr').textContent = 'Распознать текст';
                    alert('Ошибка: ' + (status.message || 'неизвестная'));
                    if (detectionBoxes.length) drawDetectionBoxes(detectionBoxes);
                } else {
                    const total = status.total_boxes || 1;
                    const done = status.boxes_done || 0;
                    const pct = Math.round((done / total) * 100);
                    updateProgress(pct);
                }
            });
    }, 2000);
}

// ---------- Отрисовка результатов OCR ----------
function renderOCR(data) {
    ocrData = data;
    // Полностью очищаем canvas
    canvas.getObjects().forEach(obj => canvas.remove(obj));
    ocrBoxes = [];

    const blocksContainer = document.getElementById('ocr-blocks');
    blocksContainer.innerHTML = '';

    if (!data.boxes || data.boxes.length === 0) {
        blocksContainer.innerHTML = '<div class="alert alert-warning">Текстовые блоки не найдены. Вы можете добавить фрагмент вручную.</div>';
        canvas.renderAll();
        return;
    }

    data.boxes.forEach((box, idx) => {
        const color = getColorByClass(box.class);
        const rect = new fabric.Rect({
            left: box.bbox.x,
            top: box.bbox.y,
            width: box.bbox.width,
            height: box.bbox.height,
            fill: 'rgba(0,0,255,0.1)',
            stroke: color,
            strokeWidth: 2,
            selectable: true,
            hoverCursor: 'pointer',
            hasControls: false,
            lockMovementX: true,
            lockMovementY: true
        });
        rect._isOCR = true;
        rect._boxIndex = idx;
        rect.on('mousedown', () => {
            const cards = document.querySelectorAll('.ocr-card');
            if (cards[idx]) {
                cards[idx].scrollIntoView({ behavior: 'smooth', block: 'center' });
                cards[idx].classList.add('border', 'border-danger');
                setTimeout(() => cards[idx].classList.remove('border', 'border-danger'), 1500);
            }
        });
        canvas.add(rect);
        ocrBoxes.push(rect);

        const card = document.createElement('div');
        card.className = 'card mb-3 ocr-card';
        card.dataset.boxId = box.box_id;
        card.dataset.boxIdx = idx;
        card.innerHTML = `
            <div class="card-header" style="background-color: ${color}; color: white;">
                Блок ${idx + 1} — ${box.class}
            </div>
            <div class="card-body p-2">
                <div class="mb-2 p-2 bg-light border rounded">
                    <strong>Весь текст:</strong>
                    <p class="mb-0 combined-text" id="combined-${idx}">${box.combined_text || ''}</p>
                </div>
                <hr>
                <div class="lines-container">
                ${box.lines.map((line, lineIdx) => `
                    <div class="row mb-2 align-items-center line-row" data-line-id="${line.line_id}">
                        <div class="col-md-2"><img src="${line.crop_url}" class="img-thumbnail" style="max-height: 40px;"></div>
                        <div class="col-md-8">
                            <textarea class="form-control form-control-sm ocr-text" data-line-id="${line.line_id}" data-box-idx="${idx}" rows="2">${line.edited_text || line.text || ''}</textarea>
                            <small class="text-muted">Уверенность: ${(line.confidence * 100).toFixed(1)}%</small>
                        </div>
                        <div class="col-md-2 text-end">
                            <button class="btn btn-sm btn-outline-danger delete-line-btn" data-line-id="${line.line_id}" data-box-idx="${idx}">✕</button>
                        </div>
                    </div>
                `).join('')}
                </div>
                <button class="btn btn-sm btn-outline-primary add-line-btn mt-2" data-box-id="${box.box_id}">+ Добавить строку</button>
            </div>`;
        blocksContainer.appendChild(card);

        card.querySelector('.add-line-btn').addEventListener('click', function(e) {
            e.stopPropagation();
            const boxId = this.dataset.boxId;
            if (manualMode) {
                alert('Завершите текущее выделение перед добавлением новой строки.');
                return;
            }
            enterDrawingModeForBox(boxId);
        });
    });

    document.querySelectorAll('.delete-line-btn').forEach(btn => {
        btn.removeEventListener('click', deleteLineHandler);
        btn.addEventListener('click', deleteLineHandler);
    });
    document.querySelectorAll('.ocr-text').forEach(ta => {
        ta.removeEventListener('input', handleTextInput);
        ta.addEventListener('input', handleTextInput);
    });

    canvas.renderAll();
}

function deleteLineHandler() {
    const lineId = this.dataset.lineId;
    const boxIdx = this.dataset.boxIdx;
    const lineRow = document.querySelector(`.line-row[data-line-id="${lineId}"]`);
    if (lineRow) lineRow.remove();
    updateCombinedText(boxIdx);
}

function handleTextInput() {
    const boxIdx = this.dataset.boxIdx;
    updateCombinedText(boxIdx);
}

function updateCombinedText(boxIdx) {
    const card = document.querySelectorAll('.ocr-card')[boxIdx];
    if (!card) return;
    const textareas = card.querySelectorAll('.ocr-text');
    const parts = Array.from(textareas).map(ta => ta.value.trim()).filter(t => t !== '');
    const combined = parts.join(' ');
    const combinedP = document.getElementById(`combined-${boxIdx}`);
    if (combinedP) combinedP.textContent = combined || '[нет текста]';
}

// ---------- Сохранение ----------
function saveOCR() {
    const lines = [];
    document.querySelectorAll('.ocr-card .line-row').forEach(row => {
        const ta = row.querySelector('.ocr-text');
        if (ta) {
            lines.push({
                line_id: ta.dataset.lineId,
                edited_text: ta.value
            });
        }
    });

    const manualLines = [];
    document.querySelectorAll('.ocr-card').forEach(card => {
        const boxId = card.dataset.boxId;
        card.querySelectorAll('.line-row').forEach(row => {
            const ta = row.querySelector('.ocr-text');
            if (ta && ta.dataset.lineId && ta.dataset.lineId.startsWith('manual_line_')) {
                manualLines.push({
                    box_id: boxId,
                    text: ta.value
                });
            }
        });
    });

    const payload = { lines: lines };
    if (manualLines.length > 0) {
        payload.manual_lines = manualLines;
    }

    fetch(API_OCR, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(data => {
        if (data.status === 'saved') alert('Сохранено!');
        else alert('Ошибка сохранения');
    })
    .catch(() => alert('Ошибка соединения'));
}