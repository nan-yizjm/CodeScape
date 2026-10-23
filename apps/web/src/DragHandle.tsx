import { useEffect, useRef, useState, type PointerEvent } from 'react';
import { useReducedMotion } from './InteractionPrimitives';
import { springProgress } from './motion';

export const PANEL_MIN_WIDTH = 320;
export const PANEL_MAX_WIDTH = 650;
export function panelDragWidth(initial: number, startX: number, currentX: number) {
  return Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, initial + startX - currentX));
}

/** Width follows the pointer immediately. The spring is confined to the grip's visual feedback. */
export function DragHandle({ width, onWidth }: { width: number; onWidth: (width: number) => void }) {
  const [grabbed, setGrabbed] = useState(false);
  const gesture = useRef<{ pointer: number; start: number; initial: number } | null>(null);
  const grip = useRef<HTMLSpanElement>(null);
  const animation = useRef<Animation | null>(null);
  const reduced = useReducedMotion();

  useEffect(() => {
    const element = grip.current;
    if (!element) return;
    const target = grabbed ? 1.5 : 1;
    const initial = new DOMMatrixReadOnly(getComputedStyle(element).transform).a;
    animation.current?.cancel();
    element.style.transform = `scaleX(${target})`;
    if (reduced || !element.animate) return;
    const endpoint = springProgress(0.32, 1200, 80);
    animation.current = element.animate(Array.from({ length: 21 }, (_, index) => ({
      offset: index / 20,
      transform: `scaleX(${initial + (target - initial) * springProgress(index / 20 * 0.32, 1200, 80) / endpoint})`,
    })), { duration: 320, easing: 'linear' });
  }, [grabbed, reduced]);
  useEffect(() => () => { animation.current?.cancel(); gesture.current = null; }, []);

  function move(event: PointerEvent<HTMLDivElement>) {
    const active = gesture.current;
    if (active?.pointer !== event.pointerId) return;
    onWidth(panelDragWidth(active.initial, active.start, event.clientX));
  }
  function finish(event: PointerEvent<HTMLDivElement>) {
    if (gesture.current?.pointer !== event.pointerId) return;
    gesture.current = null; setGrabbed(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }
  return <div className="panel-resizer panel-drag-handle" role="separator" tabIndex={0}
    aria-label="调整详情面板宽度" aria-orientation="vertical" aria-controls="workspace-detail-panel"
    aria-valuemin={PANEL_MIN_WIDTH} aria-valuemax={PANEL_MAX_WIDTH} aria-valuenow={Math.round(width)}
    aria-valuetext={`${Math.round(width)} 像素`} data-grabbed={grabbed}
    onPointerDown={event => {
      if (event.button !== 0 || gesture.current) return;
      event.preventDefault(); event.currentTarget.focus();
      event.currentTarget.setPointerCapture(event.pointerId);
      gesture.current = { pointer: event.pointerId, start: event.clientX, initial: width };
      setGrabbed(true);
    }} onPointerMove={move} onPointerUp={event => { move(event); finish(event); }}
    onPointerCancel={finish} onLostPointerCapture={finish}
    onKeyDown={event => {
      const next = event.key === 'Home' ? PANEL_MIN_WIDTH : event.key === 'End' ? PANEL_MAX_WIDTH
        : event.key === 'ArrowLeft' ? width + (event.shiftKey ? 40 : 16)
          : event.key === 'ArrowRight' ? width - (event.shiftKey ? 40 : 16) : null;
      if (next === null) return;
      event.preventDefault(); onWidth(panelDragWidth(next, 0, 0));
    }}><span className="panel-drag-grip" ref={grip} aria-hidden="true" /></div>;
}
