import { useState, useCallback } from 'react';
import {
  scanFolder, importSession, listFlights, deleteFlight, browseFolder,
  type Flight, type ScanResult, type SessionPreview,
} from '../api';

const DATA_TYPE_LABELS: Record<string, string> = {
  gps: 'GPS',
  imu: 'IMU',
  drone_state: '飞控状态',
  pos: '位置',
  engine: '发动机',
  powerbox: '电源',
  dual_antenna: '双天线',
  alert: '告警',
};

type SessionImportState = 'idle' | 'importing' | 'done' | 'error';

interface SessionState {
  importState: SessionImportState;
  errorMsg?: string;
  mode: 'overwrite' | 'as_new';
}

function sessionStateKey(droneId: string, sessionKey: string): string {
  return `${droneId}__${sessionKey}`;
}

interface Props {
  onImported: () => void;
}

export default function ImportPage({ onImported }: Props) {
  const [path, setPath] = useState('');
  const [scanning, setScanning] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);
  const [sessionStates, setSessionStates] = useState<Map<string, SessionState>>(new Map());
  const [flights, setFlights] = useState<Flight[]>([]);

  // ─── Folder browse ──────────────────────────────────────

  const handleBrowse = async () => {
    setBrowsing(true);
    try {
      const data = await browseFolder();
      if (data.path && !data.cancelled) {
        setPath(data.path);
      }
    } catch (e: any) {
      console.error('Browse failed:', e);
    } finally {
      setBrowsing(false);
    }
  };

  // ─── Scan ───────────────────────────────────────────────

  const handleScan = async () => {
    if (!path.trim()) return;
    setScanning(true);
    setScanResult(null);
    setSessionStates(new Map());
    try {
      const data = await scanFolder(path.trim());
      setScanResult(data);
    } catch (e: any) {
      setScanResult({ source_path: path, folder_name: path, sessions: [], error: '扫描失败: ' + e.message });
    } finally {
      setScanning(false);
    }
  };

  // ─── Import single session ──────────────────────────────

  const handleImportSession = async (droneId: string, sessionKey: string, mode: 'overwrite' | 'as_new') => {
    const key = sessionStateKey(droneId, sessionKey);
    setSessionStates(prev => {
      const next = new Map(prev);
      next.set(key, { importState: 'importing', mode });
      return next;
    });

    try {
      const result = await importSession(path, droneId, sessionKey, mode);
      if (result.error) {
        throw new Error(result.error);
      }
      setSessionStates(prev => {
        const next = new Map(prev);
        next.set(key, { importState: 'done', mode });
        return next;
      });
      onImported();
      loadFlights();
      // Re-scan to update import status
      refreshScan();
    } catch (e: any) {
      setSessionStates(prev => {
        const next = new Map(prev);
        next.set(key, { importState: 'error', errorMsg: e.message, mode });
        return next;
      });
    }
  };

  const refreshScan = async () => {
    if (!path.trim()) return;
    try {
      const data = await scanFolder(path.trim());
      setScanResult(data);
    } catch { /* ignore */ }
  };

  // ─── Flight list management ─────────────────────────────

  const loadFlights = useCallback(async () => {
    try {
      const data = await listFlights();
      setFlights(data.flights);
    } catch { /* ignore */ }
  }, []);

  const handleDelete = async (id: number) => {
    await deleteFlight(id);
    loadFlights();
    onImported();
    refreshScan();
  };

  // ─── Render helpers ─────────────────────────────────────

  const getSessionState = (droneId: string, sessionKey: string): SessionState => {
    return sessionStates.get(sessionStateKey(droneId, sessionKey)) || { importState: 'idle', mode: 'overwrite' };
  };

  const renderDataTypeBadges = (dataTypes: Record<string, number>) => (
    <div className="flex flex-wrap gap-1">
      {Object.entries(dataTypes).map(([type, count]) => (
        <span key={type} className="px-1.5 py-0.5 bg-gray-100 rounded text-xs text-gray-600">
          {DATA_TYPE_LABELS[type] || type} {count > 1 && `×${count}`}
        </span>
      ))}
    </div>
  );

  const renderSessionActions = (session: SessionPreview) => {
    const state = getSessionState(session.drone_id, session.session_key);

    if (state.importState === 'importing') {
      return (
        <span className="text-xs text-blue-500 animate-pulse">
          ⏳ 导入中...
        </span>
      );
    }

    if (state.importState === 'done') {
      return (
        <span className="text-xs text-green-600 font-medium">
          ✓ 已导入
        </span>
      );
    }

    if (state.importState === 'error') {
      return (
        <div className="flex items-center gap-2">
          <span className="text-xs text-red-500" title={state.errorMsg}>
            ✗ 导入失败
          </span>
          <button
            onClick={() => handleImportSession(session.drone_id, session.session_key, state.mode)}
            className="text-xs text-blue-600 hover:text-blue-500"
          >
            重试
          </button>
        </div>
      );
    }

    // idle state
    if (session.import_status === 'imported') {
      return (
        <div className="flex items-center gap-2">
          <button
            onClick={() => handleImportSession(session.drone_id, session.session_key, 'overwrite')}
            className="px-3 py-1 bg-amber-100 hover:bg-amber-200 text-amber-700 rounded text-xs font-medium transition-colors"
          >
            覆盖
          </button>
          <button
            onClick={() => handleImportSession(session.drone_id, session.session_key, 'as_new')}
            className="px-3 py-1 bg-blue-100 hover:bg-blue-200 text-blue-700 rounded text-xs font-medium transition-colors"
          >
            作为新记录导入
          </button>
        </div>
      );
    }

    return (
      <button
        onClick={() => handleImportSession(session.drone_id, session.session_key, 'overwrite')}
        className="px-3 py-1 bg-blue-600 hover:bg-blue-500 text-white rounded text-xs font-medium transition-colors"
      >
        导入
      </button>
    );
  };

  // ─── Group sessions by drone_id ─────────────────────────

  const groupedSessions = (scanResult?.sessions || []).reduce<Record<string, SessionPreview[]>>((acc, s) => {
    if (!acc[s.drone_id]) acc[s.drone_id] = [];
    acc[s.drone_id].push(s);
    return acc;
  }, {});

  // ─── UI ─────────────────────────────────────────────────

  return (
    <div className="h-full overflow-auto p-8 max-w-4xl mx-auto space-y-8">
      {/* Section 1: Folder Selection */}
      <section>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">导入飞行数据</h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="输入飞行数据文件夹路径，或点击浏览选择"
            className="flex-1 bg-white border border-gray-300 rounded-lg px-4 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          />
          <button
            onClick={handleBrowse}
            disabled={browsing}
            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 disabled:opacity-40 rounded-lg text-sm font-medium text-gray-700 transition-colors"
          >
            {browsing ? '...' : '浏览'}
          </button>
          <button
            onClick={handleScan}
            disabled={scanning || !path.trim()}
            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 disabled:opacity-40 rounded-lg text-sm font-medium text-gray-700 transition-colors"
          >
            {scanning ? '扫描中...' : '扫描'}
          </button>
        </div>
      </section>

      {/* Section 2: Scan Results */}
      {scanResult && (
        <section>
          <h3 className="text-sm font-medium text-gray-500 mb-3">
            扫描结果 — {scanResult.folder_name}
          </h3>

          {scanResult.error && scanResult.sessions.length === 0 && (
            <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
              <p className="text-sm text-gray-400">{scanResult.error}</p>
            </div>
          )}

          {Object.keys(groupedSessions).length > 0 && (
            <div className="space-y-6">
              {Object.entries(groupedSessions).map(([droneId, sessions]) => (
                <div key={droneId}>
                  <div className="flex items-center gap-2 mb-3">
                    <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
                      UAV{droneId}
                    </span>
                    <span className="text-xs text-gray-400">
                      {sessions.length} 个飞行场次
                    </span>
                  </div>

                  <div className="space-y-2 ml-2 border-l-2 border-blue-100 pl-4">
                    {sessions.map((session) => {
                      const state = getSessionState(session.drone_id, session.session_key);
                      return (
                        <div
                          key={session.session_key}
                          className={`bg-white rounded-lg p-4 border transition-colors ${
                            state.importState === 'error'
                              ? 'border-red-200 bg-red-50/30'
                              : session.import_status === 'imported' && state.importState === 'idle'
                                ? 'border-green-200 bg-green-50/20'
                                : 'border-gray-200'
                          }`}
                        >
                          <div className="flex items-start justify-between gap-4">
                            <div className="space-y-2 min-w-0">
                              <div className="flex items-center gap-3 flex-wrap">
                                <span className="text-sm font-mono text-gray-700">
                                  {session.session_key || '(默认场次)'}
                                </span>

                                {/* Status badge */}
                                {state.importState === 'idle' && (
                                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                                    session.import_status === 'imported'
                                      ? 'bg-green-100 text-green-700'
                                      : 'bg-gray-100 text-gray-500'
                                  }`}>
                                    {session.import_status === 'imported' ? '已导入' : '新'}
                                  </span>
                                )}
                                {state.importState === 'done' && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-100 text-green-700">
                                    ✓ 已导入
                                  </span>
                                )}
                                {state.importState === 'error' && (
                                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-100 text-red-700">
                                    ✗ 失败
                                  </span>
                                )}

                                {/* Existing name indicator */}
                                {session.import_status === 'imported' && session.existing_flight_name && (
                                  <span className="text-[10px] text-gray-400">
                                    当前: {session.existing_flight_name}
                                  </span>
                                )}

                                <span className="text-xs text-gray-400">
                                  {session.file_count} 个数据文件
                                </span>
                              </div>

                              {renderDataTypeBadges(session.data_types)}

                              {state.importState === 'error' && state.errorMsg && (
                                <p className="text-xs text-red-500">{state.errorMsg}</p>
                              )}
                            </div>

                            <div className="shrink-0">
                              {renderSessionActions(session)}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Section 3: Imported Flights */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900">已导入飞行</h2>
          <button onClick={loadFlights} className="text-xs text-blue-600 hover:text-blue-500">
            刷新
          </button>
        </div>
        {flights.length === 0 ? (
          <p className="text-sm text-gray-400">暂无已导入的飞行数据</p>
        ) : (
          <div className="space-y-2">
            {flights.map((f) => (
              <div key={f.id} className="flex items-center justify-between bg-white rounded-lg px-4 py-3 border border-gray-200">
                <div className="flex items-center gap-4">
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
                    UAV{f.drone_id}
                  </span>
                  <span className="text-sm font-medium text-gray-800">{f.name}</span>
                  {f.session_key && (
                    <span className="text-xs text-gray-400 font-mono">{f.session_key}</span>
                  )}
                  <span className="text-xs text-gray-400">{f.flight_date}</span>
                  {f.duration_sec && (
                    <span className="text-xs text-gray-400">
                      {Math.round(f.duration_sec / 60)}分钟
                    </span>
                  )}
                </div>
                <button
                  onClick={() => handleDelete(f.id)}
                  className="text-xs text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50"
                >
                  删除
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
