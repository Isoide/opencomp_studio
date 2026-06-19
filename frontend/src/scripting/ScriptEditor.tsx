import { Play, X } from "lucide-react";

type Props = {
  open: boolean;
  code: string;
  output: string;
  isRunning: boolean;
  onCodeChange: (code: string) => void;
  onRun: () => void;
  onClose: () => void;
};

export function ScriptEditor({ open, code, output, isRunning, onCodeChange, onRun, onClose }: Props) {
  if (!open) return null;

  return (
    <div
      className="script-editor-backdrop"
      role="presentation"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="script-editor-dialog" role="dialog" aria-modal="true" aria-label="Python Script Editor">
        <div className="script-editor-header">
          <strong>Python Script Editor</strong>
          <div className="panel-actions">
            <button onClick={onRun} disabled={isRunning}>
              <Play size={14} />
              {isRunning ? "Running" : "Run"}
            </button>
            <button onClick={onClose} title="Close script editor">
              <X size={14} />
            </button>
          </div>
        </div>
        <textarea
          className="script-editor-code"
          spellCheck={false}
          value={code}
          onChange={(event) => onCodeChange(event.target.value)}
        />
        <pre className="script-editor-output">{output || "Ready."}</pre>
      </section>
    </div>
  );
}
