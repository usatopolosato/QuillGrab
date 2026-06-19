// static/js/annotate.js

let canvas;
let drawingMode = false;
let drawStart = null;
let tempRect = null;

window.addEventListener('load', () => {
    canvas = new fabric.Canvas('c', {
        selection: true,
        preserveObjectStacking: true
    });

    fabric.Image.fromURL(IMAGE_URL, (img) => {
        canvas.setWidth(img.width);
        canvas.setHeight(img.height);
        canvas.setBackgroundImage(img, canvas.renderAll.bind(canvas));
        loadDetections();
    }, { crossOrigin: 'anonymous' });

    document.getElementById('btn-detect').addEventListener('click', runDetection);
    document.getElementById('btn-save').addEventListener('click', saveAnnotations);
    document.getElementById('btn-delete-selected').addEventListener('click', deleteSelected);
    document.getElementById('btn-add-box').addEventListener('click', toggleDrawingMode);
    document.getElementById('btn-change-class').addEventListener('click', changeClassForSelected);
    document.getElementById('btn-prev-page').addEventListener('click', () => navigate(PAGE_INDEX - 1));
    document.getElementById('btn-next-page').addEventListener('click', () => navigate(PAGE_INDEX + 1));

    window.addEventListener('keydown', onKeyDown);
    canvas.on('mouse:down', onMouseDown);
    canvas.on('mouse:move', onMouseMove);
    canvas.on('mouse:up', onMouseUp);
    canvas.on('mouse:dblclick', onDoubleClick);
});

function loadDetections() {
    fetch(DETECTIONS_API + '?edited=true')
        .then(res => res.ok ? res.json() : Promise.reject())
        .then(data => drawBoxes(data.detections))
        .catch(() => {
            fetch(DETECTIONS_API + '?edited=false')
                .then(res => res.ok ? res.json() : null)
                .then(data => data && data.detections && drawBoxes(data.detections))
                .catch(() => console.log('Нет готовых детекций'));
        });
}

function getStrokeColor(cls) {
    switch (cls) {
        case 'text': return '#0066ff';   // синий
        case 'image': return '#00aa00';  // зелёный
        case 'title': return '#ff6600';  // оранжевый
        default: return '#cccccc';
    }
}

function drawBoxes(boxList) {
    canvas.getObjects().forEach(obj => canvas.remove(obj));
    boxList.forEach(box => {
        const cls = box.class;
        const stroke = getStrokeColor(cls);
        const rect = new fabric.Rect({
            left: box.x, top: box.y,
            width: box.width, height: box.height,
            fill: 'rgba(0,0,255,0.1)',
            stroke: stroke,
            strokeWidth: 2,
            selectable: true,
            hasControls: true,
            lockUniScaling: false
        });
        rect.customData = { id: box.id, class: cls, confidence: box.confidence || 0 };
        const label = new fabric.Text(cls, {
            left: box.x, top: box.y - 15,
            fontSize: 12, fill: 'white',
            backgroundColor: 'rgba(0,0,0,0.6)',
            selectable: false, evented: false
        });
        rect._label = label;
        rect.on('moving', () => label.set({ left: rect.left, top: rect.top - 15 }));
        rect.on('scaling', () => label.set({ left: rect.left, top: rect.top - 15 }));
        rect.on('removed', () => canvas.remove(rect._label));
        canvas.add(rect);
        canvas.add(label);
    });
    canvas.renderAll();
}

function runDetection() {
    const btn = document.getElementById('btn-detect');
    btn.disabled = true;
    btn.textContent = 'Идёт детекция...';
    fetch(DETECT_API, { method: 'POST' })
        .then(res => res.json())
        .then(data => data.detections ? drawBoxes(data.detections) : alert('Ошибка детекции'))
        .catch(err => alert('Ошибка: ' + err))
        .finally(() => {
            btn.disabled = false;
            btn.textContent = 'Найти объекты';
        });
}

function saveAnnotations() {
    const boxes = [];
    canvas.getObjects().forEach(obj => {
        if (obj.customData) {
            boxes.push({
                id: obj.customData.id,
                class: obj.customData.class,
                x: Math.round(obj.left),
                y: Math.round(obj.top),
                width: Math.round(obj.getScaledWidth()),
                height: Math.round(obj.getScaledHeight()),
                confidence: obj.customData.confidence
            });
        }
    });
    fetch(DETECTIONS_API, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ detections: boxes })
    })
    .then(res => res.json())
    .then(data => alert(data.status === 'saved' || data.status === 'ok' ? 'Разметка сохранена' : 'Ошибка сохранения'))
    .catch(() => alert('Ошибка соединения'));
}

function deleteSelected() {
    const active = canvas.getActiveObject();
    if (!active || !active.customData) return;
    if (active._label) canvas.remove(active._label);
    canvas.remove(active);
    canvas.discardActiveObject();
    canvas.renderAll();
}

function onKeyDown(e) {
    if ((e.key === 'Delete' || e.key === 'Backspace') &&
        document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
        deleteSelected();
        e.preventDefault();
    }
}

function toggleDrawingMode() {
    drawingMode = !drawingMode;
    const btn = document.getElementById('btn-add-box');
    btn.textContent = drawingMode ? 'Завершить рисование' : 'Добавить рамку';
    canvas.selection = !drawingMode;
    canvas.defaultCursor = drawingMode ? 'crosshair' : 'default';
}

function onMouseDown(opt) {
    if (!drawingMode) return;
    const pointer = canvas.getPointer(opt.e);
    drawStart = { x: pointer.x, y: pointer.y };
    tempRect = new fabric.Rect({
        left: pointer.x, top: pointer.y,
        width: 0, height: 0,
        fill: 'rgba(255,0,0,0.2)', stroke: 'red',
        strokeDashArray: [5, 5]
    });
    canvas.add(tempRect);
}

function onMouseMove(opt) {
    if (!drawingMode || !tempRect) return;
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
    if (!drawingMode || !tempRect) return;
    const finalRect = {
        x: tempRect.left,
        y: tempRect.top,
        width: tempRect.width * (1 / canvas.getZoom()),
        height: tempRect.height * (1 / canvas.getZoom())
    };
    canvas.remove(tempRect);
    tempRect = null;
    showClassDialog(finalRect);
}

function showClassDialog(coords) {
    const oldModal = document.getElementById('classModal');
    if (oldModal) oldModal.remove();
    const modalHtml = `
    <div class="modal fade" id="classModal" tabindex="-1">
      <div class="modal-dialog modal-sm">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Выберите класс</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body text-center">
            <button class="btn btn-primary m-1" data-class="text">Текст</button>
            <button class="btn btn-success m-1" data-class="image">Изображение</button>
            <button class="btn btn-warning m-1" data-class="title">Заголовок</button>
          </div>
        </div>
      </div>
    </div>`;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    const modalEl = document.getElementById('classModal');
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
    modalEl.addEventListener('click', (e) => {
        if (e.target.dataset.class) {
            addNewBox(coords, e.target.dataset.class);
            modal.hide();
            modalEl.remove();
        }
    });
    modalEl.addEventListener('hidden.bs.modal', () => modalEl.remove());
}

function addNewBox({ x, y, width, height }, cls) {
    const id = 'box_' + Date.now();
    const stroke = getStrokeColor(cls);
    const rect = new fabric.Rect({
        left: x, top: y, width, height,
        fill: 'rgba(0,0,255,0.1)',
        stroke: stroke,
        strokeWidth: 2, selectable: true, hasControls: true
    });
    rect.customData = { id, class: cls, confidence: 1.0 };
    const label = new fabric.Text(cls, {
        left: x, top: y - 15, fontSize: 12,
        fill: 'white', backgroundColor: 'rgba(0,0,0,0.6)',
        selectable: false, evented: false
    });
    rect._label = label;
    rect.on('moving', () => label.set({ left: rect.left, top: rect.top - 15 }));
    rect.on('scaling', () => label.set({ left: rect.left, top: rect.top - 15 }));
    rect.on('removed', () => canvas.remove(rect._label));
    canvas.add(rect);
    canvas.add(label);
    canvas.renderAll();
}

function changeClassForSelected() {
    const active = canvas.getActiveObject();
    if (!active || !active.customData) {
        alert('Выделите рамку');
        return;
    }
    const oldClass = active.customData.class;
    const oldModal = document.getElementById('classChangeModal');
    if (oldModal) oldModal.remove();
    const modalHtml = `
    <div class="modal fade" id="classChangeModal" tabindex="-1">
      <div class="modal-dialog modal-sm">
        <div class="modal-content">
          <div class="modal-header">
            <h5 class="modal-title">Сменить класс</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
          </div>
          <div class="modal-body text-center">
            <button class="btn ${oldClass==='text'?'active':''} btn-outline-primary" data-newclass="text">Текст</button>
            <button class="btn ${oldClass==='image'?'active':''} btn-outline-success" data-newclass="image">Изображение</button>
            <button class="btn ${oldClass==='title'?'active':''} btn-outline-warning" data-newclass="title">Заголовок</button>
          </div>
        </div>
      </div>
    </div>`;
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    const modalEl = document.getElementById('classChangeModal');
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
    modalEl.addEventListener('click', (e) => {
        if (e.target.dataset.newclass) {
            const newClass = e.target.dataset.newclass;
            active.customData.class = newClass;
            active.set('stroke', getStrokeColor(newClass));
            if (active._label) active._label.set('text', newClass);
            canvas.renderAll();
            modal.hide();
            modalEl.remove();
        }
    });
    modalEl.addEventListener('hidden.bs.modal', () => modalEl.remove());
}

function onDoubleClick(opt) {
    const target = opt.target;
    if (!target || !target.customData) return;
    changeClassForSelected();
}

function navigate(newPage) {
    if (newPage < 1 || newPage > TOTAL_PAGES) return;
    window.location.href = `/annotate/${PROJECT_ID}/${newPage}`;
}