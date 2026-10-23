import { useEffect, useRef } from 'react';
import { EditorState } from '@codemirror/state';
import { EditorView, Decoration, lineNumbers, highlightActiveLineGutter } from '@codemirror/view';
import { syntaxHighlighting, defaultHighlightStyle, HighlightStyle } from '@codemirror/language';
import { python } from '@codemirror/lang-python';
import { cpp } from '@codemirror/lang-cpp';
import { javascript } from '@codemirror/lang-javascript';
import type { SourceFile, Location, ScrollPosition } from './types';
import './canvas-material.css';

// 复用 CodeMirror 已有语义标签，仅替换其固定浅色；CSS 切换外观不重建阅读位置。
const syntaxColors: Record<string, string> = {
  '#404740': 'var(--syntax-meta)', '#708': 'var(--syntax-keyword)', '#219': 'var(--syntax-number)',
  '#164': 'var(--syntax-literal)', '#a11': 'var(--syntax-string)', '#e40': 'var(--syntax-string)',
  '#00f': 'var(--syntax-function)', '#30a': 'var(--syntax-variable)', '#085': 'var(--syntax-type)',
  '#167': 'var(--syntax-type)', '#256': 'var(--syntax-variable)', '#00c': 'var(--syntax-function)',
  '#940': 'var(--syntax-comment)', '#f00': 'var(--syntax-invalid)',
};
const sourceHighlightStyle = HighlightStyle.define(defaultHighlightStyle.specs.map(spec => ({
  ...spec, ...(spec.color ? { color: syntaxColors[String(spec.color)] || 'var(--ink)' } : {}),
  ...(spec.fontWeight ? { fontWeight: 'var(--weight-bold)' } : {}),
})));

export function SourceReader({ file, location, position, onPosition }: { file: SourceFile; location: Location;
  position?: ScrollPosition; onPosition?: (position: ScrollPosition)=>void }) {
  const element = useRef<HTMLDivElement>(null);
  const reporter = useRef(onPosition); reporter.current = onPosition;
  const initialPosition = useRef(position);
  useEffect(() => {
    if (!element.current) return;
    const language = file.path.endsWith('.py') ? python() : /\.(tsx?|jsx?)$/.test(file.path) ? javascript({ typescript: true }) : cpp();
    const document = EditorState.create({ doc: file.text });
    const start = document.doc.line(Math.min(location.start_line, document.doc.lines)).from;
    const end = document.doc.line(Math.min(location.end_line, document.doc.lines)).to;
    const decoration = Decoration.set(end > start ? [Decoration.mark({ class: 'source-highlight' }).range(start, end)] : []);
    const view = new EditorView({ parent: element.current, state: EditorState.create({ doc: file.text, extensions: [
      EditorState.readOnly.of(true), EditorView.editable.of(false), lineNumbers(), highlightActiveLineGutter(),
      syntaxHighlighting(sourceHighlightStyle), language, EditorView.decorations.of(decoration),
      EditorView.theme({ '&': { height: '100%', fontSize: '13px', fontWeight: 'var(--weight-regular)', color: 'var(--ink)', backgroundColor: 'var(--surface)' }, '.cm-scroller': { overflow: 'auto', fontFamily: 'var(--font-code)', lineHeight: '1.8', fontVariantNumeric: 'tabular-nums' },
        '.cm-content': { padding: '16px 0' }, '.cm-gutters': { background: 'var(--soft)', borderRight: '0', color: 'var(--muted)' },
        '.cm-line': { paddingLeft: '12px' }, '.cm-activeLineGutter': { color: 'var(--ink)', backgroundColor: 'var(--source-active-line)' },
        '&.cm-focused': { outline: 'none' } }),
    ] }) });
    view.scrollDOM.tabIndex = 0;
    view.scrollDOM.setAttribute('role','region');
    view.scrollDOM.setAttribute('aria-label','只读源码滚动区域');
    view.dispatch({ effects: EditorView.scrollIntoView(start, { y: 'center' }) });
    let restoring = true;
    let secondFrame = 0;
    const frame = requestAnimationFrame(()=> {
      const saved = initialPosition.current;
      if (saved?.line) view.dispatch({effects:EditorView.scrollIntoView(view.state.doc.line(Math.min(saved.line,view.state.doc.lines)).from,{y:'start'})});
      secondFrame = requestAnimationFrame(()=> {
        if (saved) {view.scrollDOM.scrollTop = saved.top; view.scrollDOM.scrollLeft = saved.left;}
        restoring = false;
      });
    });
    const changed = () => {
      if (restoring) return;
      reporter.current?.({top:view.scrollDOM.scrollTop,left:view.scrollDOM.scrollLeft,
        line:view.state.doc.lineAt(view.lineBlockAtHeight(view.scrollDOM.scrollTop).from).number});
    };
    view.scrollDOM.addEventListener('scroll',changed);
    return () => {
      changed(); cancelAnimationFrame(frame); cancelAnimationFrame(secondFrame);
      view.scrollDOM.removeEventListener('scroll',changed); view.destroy();
    };
  }, [file, location]);
  return <div className="source-reader" ref={element} />;
}
