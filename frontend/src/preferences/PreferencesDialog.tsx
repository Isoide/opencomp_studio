import { X } from "lucide-react";

import type { Project, ProjectPreferences } from "../api/client";
import { projectHotkeys, readPreloadEnabled, readPreloadMaxFrames, viewerTransferPrecision } from "../projectPreferences";

type Props = {
  project: Project;
  onChange: (patch: Partial<ProjectPreferences>) => void;
  onClose: () => void;
  onSave: () => void;
};

export function PreferencesDialog({ project, onChange, onClose, onSave }: Props) {
  const preferences = project.preferences;
  const hotkeys = projectHotkeys(project) ?? preferences.hotkeys;

  return (
    <div
      className="preferences-backdrop"
      role="presentation"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section className="preferences-dialog" role="dialog" aria-modal="true" aria-label="Preferences">
        <div className="prefs-header">
          <strong>Preferences</strong>
          <div className="panel-actions">
            <button onClick={onSave}>Save Preferences</button>
            <button onClick={onClose} title="Close preferences">
              <X size={14} />
            </button>
          </div>
        </div>
        <div className="prefs-grid">
          <label>
            Autosave
            <input type="number" value={preferences.autosave_seconds} onChange={(event) => onChange({ autosave_seconds: Number(event.target.value) })} />
          </label>
          <label>
            Idle Autosave
            <input
              type="number"
              value={preferences.idle_autosave_seconds}
              onChange={(event) => onChange({ idle_autosave_seconds: Number(event.target.value) })}
            />
          </label>
          <label>
            Cache MB
            <input type="number" value={preferences.cache_memory_limit_mb} onChange={(event) => onChange({ cache_memory_limit_mb: Number(event.target.value) })} />
          </label>
          <label className="toggle-label">
            <input type="checkbox" checked={readPreloadEnabled(project)} onChange={(event) => onChange({ read_preload_enabled: event.target.checked })} />
            Preload Reads
          </label>
          <label>
            Read Preload Frames
            <input type="number" min={1} value={readPreloadMaxFrames(project)} onChange={(event) => onChange({ read_preload_max_frames: Number(event.target.value) })} />
          </label>
          <label>
            Playback Transfer
            <select
              value={preferences.playback_transfer_mode}
              onChange={(event) => onChange({ playback_transfer_mode: event.target.value as Project["preferences"]["playback_transfer_mode"] })}
            >
              <option value="hybrid-preview">GPU Float + Cache</option>
              <option value="always-float">Always Float</option>
              <option value="fast-display">Fast Display PNG</option>
            </select>
          </label>
          <label>
            Viewer Precision
            <select
              value={viewerTransferPrecision(project)}
              onChange={(event) => onChange({ viewer_transfer_precision: event.target.value as Project["preferences"]["viewer_transfer_precision"] })}
            >
              <option value="float32">Float 32</option>
              <option value="float16">Half Float 16</option>
              <option value="rgb10a2">10-bit Preview</option>
              <option value="uint8">8-bit Preview</option>
            </select>
          </label>
          <label>
            Zoom Speed
            <input type="number" step="0.05" value={preferences.viewer_zoom_speed} onChange={(event) => onChange({ viewer_zoom_speed: Number(event.target.value) })} />
          </label>
          <label className="toggle-label">
            <input type="checkbox" checked={preferences.wheel_zoom_enabled} onChange={(event) => onChange({ wheel_zoom_enabled: event.target.checked })} />
            Wheel Zoom
          </label>
          <label className="toggle-label">
            <input
              type="checkbox"
              checked={preferences.auto_connect_new_nodes}
              onChange={(event) => onChange({ auto_connect_new_nodes: event.target.checked })}
            />
            Auto Connect
          </label>
          <label>
            Read Hotkey
            <input value={hotkeys.add_read} onChange={(event) => onChange({ hotkeys: { ...hotkeys, add_read: event.target.value } })} />
          </label>
          <label>
            Write Hotkey
            <input value={hotkeys.add_write} onChange={(event) => onChange({ hotkeys: { ...hotkeys, add_write: event.target.value } })} />
          </label>
          <label>
            Merge Hotkey
            <input value={hotkeys.add_merge} onChange={(event) => onChange({ hotkeys: { ...hotkeys, add_merge: event.target.value } })} />
          </label>
          <label>
            Group Hotkey
            <input value={hotkeys.add_group} onChange={(event) => onChange({ hotkeys: { ...hotkeys, add_group: event.target.value } })} />
          </label>
          <label>
            Disable Hotkey
            <input value={hotkeys.toggle_disable ?? "d"} onChange={(event) => onChange({ hotkeys: { ...hotkeys, toggle_disable: event.target.value } })} />
          </label>
          <label>
            Init Scripts
            <input
              value={preferences.custom_init_scripts.join(";")}
              onChange={(event) =>
                onChange({
                  custom_init_scripts: event.target.value
                    .split(";")
                    .map((item) => item.trim())
                    .filter(Boolean),
                })
              }
              placeholder="script_a.py;script_b.py"
            />
          </label>
        </div>
      </section>
    </div>
  );
}
