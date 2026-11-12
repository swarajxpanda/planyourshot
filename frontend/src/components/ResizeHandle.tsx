import { useEffect, useRef, useState } from "react";

interface Props {
  label: string;
  /** id of the panel whose width this handle controls */
  controls: string;
  value: number;
  min: number;
  max: number;
  defaultValue: number;
  /** 1 when dragging right grows the panel (panel on the left), -1 when it shrinks it (panel on the right) */
  direction: 1 | -1;
  onChange: (width: number) => void;
}

/**
 * Vertical splitter between two columns. Drag with the pointer, use arrow keys (Shift for
 * bigger steps, Home/End for the limits), or double-click to reset to the default width.
 */
export default function ResizeHandle({ label, controls, value, min, max, defaultValue, direction, onChange }: Props) {
  const [dragging, setDragging] = useState(false);
  const start = useRef({ x: 0, width: 0 });
  const clamp = (w: number) => Math.round(Math.min(max, Math.max(min, w)));

  // Keep the resize cursor and suppress text selection while the pointer is anywhere on the page.
  useEffect(() => {
    if (!dragging) return;
    document.body.classList.add("col-resizing");
    return () => document.body.classList.remove("col-resizing");
  }, [dragging]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 48 : 16;
    const next =
      e.key === "ArrowRight" ? value + step * direction
      : e.key === "ArrowLeft" ? value - step * direction
      : e.key === "Home" ? min
      : e.key === "End" ? max
      : null;
    if (next === null) return;
    e.preventDefault();
    onChange(clamp(next));
  };

  return (
    <div
      className={dragging ? "resize-handle dragging" : "resize-handle"}
      role="separator" aria-orientation="vertical" aria-label={label} aria-controls={controls}
      aria-valuenow={value} aria-valuemin={min} aria-valuemax={max} tabIndex={0}
      title="Drag to resize · double-click to reset"
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        start.current = { x: e.clientX, width: value };
        setDragging(true);
      }}
      onPointerMove={(e) => { if (dragging) onChange(clamp(start.current.width + (e.clientX - start.current.x) * direction)); }}
      onPointerUp={() => setDragging(false)}
      onPointerCancel={() => setDragging(false)}
      onLostPointerCapture={() => setDragging(false)}
      onDoubleClick={() => onChange(clamp(defaultValue))}
      onKeyDown={onKeyDown}
    />
  );
}
