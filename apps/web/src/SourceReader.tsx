import { useEffect, useRef } from 'react';
import { EditorState } from '@codemirror/state';
import { EditorView, Decoration, lineNumbers, highlightActiveLineGutter } from '@codemirror/view';
import { syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language';
import { python } from '@codemirror/lang-python';
import { cpp } from '@codemirror/lang-cpp';
import { javascript } from '@codemirror/lang-javascript';
import type { SourceFile, Location } from './types';

export function SourceReader({ file, location }: { file: SourceFile; location: Location }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current) return;
    const language = file.path.endsWith('.py') ? python() : /\.(tsx?|jsx?)$/.test(file.path) ? javascript({ typescript: true }) : cpp();
    const document = EditorState.create({ doc: file.text });
    const start = document.doc.line(Math.min(location.start_line, document.doc.lines)).from;
    const end = document.doc.line(Math.min(location.end_line, document.doc.lines)).to;
    const decoration = Decoration.set(end > start ? [Decoration.mark({ class: 'source-highlight' }).range(start, end)] : []);
    const view = new EditorView({ parent: element.current, state: EditorState.create({ doc: file.text, extensions: [
      EditorState.readOnly.of(true), EditorView.editable.of(false), lineNumbers(), highlightActiveLineGutter(),
      syntaxHighlighting(defaultHighlightStyle), language, EditorView.decorations.of(decoration),
      EditorView.theme({ '&': { height: '100%', fontSize: '13px' }, '.cm-scroller': { overflow: 'auto', fontFamily: 'Consolas, monospace' },
        '.cm-content': { padding: '18px 0' }, '.cm-gutters': { background: '#fafafa', border: 'none', color: '#777777' },
        '.cm-line': { paddingLeft: '12px' }, '&.cm-focused': { outline: 'none' } }),
    ] }) });
    view.dispatch({ effects: EditorView.scrollIntoView(start, { y: 'center' }) });
    return () => view.destroy();
  }, [file, location]);
  return <div className="source-reader" ref={element} />;
}
