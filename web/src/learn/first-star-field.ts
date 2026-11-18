import { clampCamera, mapScale, toScreen, toWorld, zoomAt, type Camera, type Point, type Size } from "../play/hunt-model";

type Mark = { at: Point; label: string; kind: "landmark" | "candidate" | "archive" };
type FieldView = { image: HTMLCanvasElement; marks: Mark[]; clue?: Point & Size };

export function mountFirstStarField(
  canvas: HTMLCanvasElement,
  world: Size,
  read: () => FieldView,
  pick: (point: Point) => void,
  onZoom: (magnification: number) => void,
) {
  const ctx = canvas.getContext("2d")!;
  const initial = { x: world.width / 2, y: world.height / 2, zoom: 1 };
  let camera: Camera = initial;
  let screen: Size = { width: 1, height: 1 };
  let drag: { id: number; start: Point; last: Point; moved: boolean } | undefined;
  let keyboard = false;
  const listeners = new AbortController();
  const { signal } = listeners;

  function draw() {
    const { image, marks, clue } = read();
    ctx.setTransform(canvas.width / screen.width, 0, 0, canvas.height / screen.height, 0, 0);
    ctx.fillStyle = "#141d28";
    ctx.fillRect(0, 0, screen.width, screen.height);
    const scale = mapScale(camera, world, screen);
    const origin = toScreen({ x: 0, y: 0 }, camera, world, screen);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(image, origin.x, origin.y, world.width * scale, world.height * scale);
    ctx.save();
    ctx.beginPath();
    ctx.rect(origin.x, origin.y, world.width * scale, world.height * scale);
    ctx.clip();
    if (clue) {
      const point = toScreen(clue, camera, world, screen);
      ctx.setLineDash([6, 5]);
      ctx.strokeStyle = "#af5600";
      ctx.lineWidth = 2;
      ctx.strokeRect(point.x, point.y, clue.width * scale, clue.height * scale);
      ctx.setLineDash([]);
    }
    ctx.font = "600 12px system-ui";
    for (const mark of marks) {
      const point = toScreen(mark.at, camera, world, screen);
      const radius = mark.kind === "landmark" ? 10 : mark.kind === "archive" ? 21 : 15;
      const color = mark.kind === "landmark" ? "#71caff" : mark.kind === "archive" ? "#8ff0bd" : "#ffd485";
      ctx.beginPath();
      ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
      if (mark.kind === "archive") ctx.setLineDash([5, 3]);
      ctx.strokeStyle = "#19222c";
      ctx.lineWidth = 4;
      ctx.stroke();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.strokeStyle = "#19222c";
      ctx.lineWidth = 3;
      const labelX = point.x + radius + 4;
      const labelY = point.y + (mark.kind === "candidate" ? radius + 8 : -5);
      ctx.strokeText(mark.label, labelX, labelY);
      ctx.fillStyle = color;
      ctx.fillText(mark.label, labelX, labelY);
    }
    if (keyboard) {
      const x = screen.width / 2;
      const y = screen.height / 2;
      ctx.beginPath();
      ctx.moveTo(x - 9, y); ctx.lineTo(x + 9, y);
      ctx.moveTo(x, y - 9); ctx.lineTo(x, y + 9);
      ctx.lineWidth = 4;
      ctx.strokeStyle = "#19222c";
      ctx.stroke();
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
    }
    ctx.restore();
  }

  function resize() {
    const { width, height } = canvas.getBoundingClientRect();
    if (!(width && height)) return;
    screen = { width, height };
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    camera = clampCamera(camera, world, screen);
    draw();
  }

  function zoom(factor: number, anchor = { x: screen.width / 2, y: screen.height / 2 }) {
    camera = zoomAt(camera, camera.zoom * factor, anchor, world, screen);
    draw();
    onZoom(camera.zoom);
  }

  function move(x: number, y: number) {
    const scale = mapScale(camera, world, screen);
    camera = clampCamera({ ...camera, x: camera.x + x / scale, y: camera.y + y / scale }, world, screen);
    draw();
  }

  function local(event: PointerEvent | WheelEvent) {
    const bounds = canvas.getBoundingClientRect();
    return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
  }

  function select(at: Point) {
    if (at.x >= 0 && at.x < world.width && at.y >= 0 && at.y < world.height) pick(at);
  }

  canvas.addEventListener("pointerdown", event => {
    if (event.button !== 0 || !event.isPrimary) return;
    canvas.focus({ preventScroll: true });
    canvas.setPointerCapture(event.pointerId);
    const at = local(event);
    keyboard = false;
    drag = { id: event.pointerId, start: at, last: at, moved: false };
  }, { signal });
  canvas.addEventListener("pointermove", event => {
    if (drag?.id !== event.pointerId) return;
    const at = local(event);
    drag.moved ||= Math.hypot(at.x - drag.start.x, at.y - drag.start.y) > 5;
    if (!drag.moved) return;
    move(drag.last.x - at.x, drag.last.y - at.y);
    drag.last = at;
  }, { signal });
  canvas.addEventListener("pointerup", event => {
    if (drag?.id !== event.pointerId) return;
    if (!drag.moved) select(toWorld(local(event), camera, world, screen));
    drag = undefined;
  }, { signal });
  canvas.addEventListener("lostpointercapture", () => { drag = undefined; }, { signal });
  canvas.addEventListener("wheel", event => {
    if (!(event.ctrlKey || event.metaKey)) return;
    event.preventDefault();
    zoom(Math.exp(-event.deltaY * .01), local(event));
  }, { signal, passive: false });
  canvas.addEventListener("keydown", event => {
    const panning: Record<string, [number, number]> = {
      ArrowLeft: [-24, 0], ArrowUp: [0, -24], ArrowRight: [24, 0], ArrowDown: [0, 24],
    };
    const delta = panning[event.key];
    if (!(delta || ["Enter", "+", "=", "-"].includes(event.key))) return;
    event.preventDefault();
    keyboard = true;
    if (delta) move(...delta);
    else if (event.key === "Enter") select(camera);
    else zoom(event.key === "-" ? 1 / 1.5 : 1.5);
    draw();
  }, { signal });
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  return {
    draw, zoom,
    reset() { camera = initial; draw(); },
    focus(at: Point, magnification: number) {
      camera = clampCamera({ ...at, zoom: Math.max(camera.zoom, magnification) }, world, screen);
      draw();
    },
    destroy() { listeners.abort(); observer.disconnect(); },
  };
}
