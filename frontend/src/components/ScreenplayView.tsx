import { memo, useMemo } from "react";
import type { Scene } from "../types";
import { formatScreenplay } from "./screenplay";

function ScreenplayView({ content, scenes }: { content: string; scenes: Scene[] }) {
  const lines = useMemo(() => formatScreenplay(content, scenes), [content, scenes]);
  return (
    <article className="screenplay" aria-label="Screenplay">
      {lines.map((l, i) =>
        l.kind === "blank" ? <div key={i} className="sp-blank" /> : (
          <p key={i} className={`sp-${l.kind}`} id={l.scene ? `scene-${l.scene}` : undefined}>
            {l.kind === "heading" && <span className="sp-num">{l.scene}</span>}
            {l.text}
          </p>
        ),
      )}
    </article>
  );
}

export default memo(ScreenplayView);
