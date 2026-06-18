import { useState, useCallback, useEffect } from 'react';
import {
  scanFolder, importSession, listFlights, deleteFlight, updateFlight, browseFolder,
  listModels, createModelFromScan, listAircraft, createAircraft, listSubdirs,
  type Flight, type ScanResult, type SessionPreview,
  type AircraftModel, type Aircraft,
} from '../api';

const DATA_TYPE_LABELS: Record<string, string> = {
  gps: 'GPS', imu: 'IMU', drone_state: '飞控状态', pos: '位置',
  engine: '发动机', powerbox: '电源', dual_antenna: '双天线', alert: '告警',
  avionics: '航电', controller: '舵机', fan_control: '风扇', gps_compare: 'GPS对比',
};

function formatBadgeStyle(fmt: string): string {
  const colors = [
    'bg-blue-100 text-blue-700 border-blue-200',
    'bg-green-100 text-green-700 border-green-200',
    'bg-amber-100 text-amber-700 border-amber-200',
    'bg-purple-100 text-purple-700 border-purple-200',
    'bg-pink-100 text-pink-700 border-pink-200',
    'bg-teal-100 text-teal-700 border-teal-200',
  ];
  let hash = 0;
  for (let i = 0; i < fmt.length; i++) {
    hash = ((hash << 5) - hash) + fmt.charCodeAt(i);
    hash |= 0;
  }
  return colors[Math.abs(hash) % colors.length];
}

interface Props {
  onImported: () => void;
}

// ── Directory structure validation ─────────────────────────

function parseDirStructure(sourcePath: string, subdirs?: string[] | null): {
  valid: boolean;
  flightDate?: string;
  aircraftSerials?: string[];
  message: string;
  level: 'ok' | 'warn' | 'error';
} {
  if (!sourcePath.trim()) {
    return { valid: false, message: '', level: 'ok' };
  }
  // Normalize path separators
  const parts = sourcePath.replace(/\\/g, '/').split('/').filter(Boolean);

  // Find date directory (starts with 8 digits)
  let dateIdx = -1;
  for (let i = 0; i < parts.length; i++) {
    if (/^\d{8}/.test(parts[i])) {
      dateIdx = i;
      break;
    }
  }

  if (dateIdx < 0) {
    return {
      valid: false,
      message: '目录结构不符合规范：第一层目录需以 YYYYMMDD（8位日期）开头，例如 20250323_test_flight/',
      level: 'error',
    };
  }

  const dateRaw = parts[dateIdx].substring(0, 8);
  const flightDate = `${dateRaw.substring(0, 4)}-${dateRaw.substring(4, 6)}-${dateRaw.substring(6, 8)}`;

  // Use filesystem subdirectories (from API) as aircraft serials
  if (subdirs && subdirs.length > 0) {
    return {
      valid: true,
      flightDate,
      aircraftSerials: subdirs,
      message: `日期: ${flightDate}，飞机序号: ${subdirs.join(', ')}`,
      level: 'ok',
    };
  }

  // Serial from path string (if source_path goes deeper than date dir)
  const serialIdx = dateIdx + 1;
  if (serialIdx < parts.length) {
    const aircraftSerial = parts[serialIdx];
    return {
      valid: true,
      flightDate,
      aircraftSerials: [aircraftSerial],
      message: `日期: ${flightDate}，飞机序号: ${aircraftSerial}`,
      level: 'ok',
    };
  }

  // No subdirectories found on disk and no serial in path
  if (subdirs !== undefined) {
    // Filesystem was checked — truly nothing there
    return {
      valid: true,
      flightDate,
      message: `日期: ${flightDate}，未找到飞机序号子目录`,
      level: 'warn',
    };
  }

  // Still waiting for filesystem check
  return {
    valid: true,
    flightDate,
    message: `日期: ${flightDate}`,
    level: 'ok',
  };
}

function DirStructureBanner({ sourcePath, scanResult }: { sourcePath: string; scanResult?: ScanResult | null }) {
  const [subdirs, setSubdirs] = useState<string[] | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    if (!sourcePath.trim()) {
      setSubdirs(undefined);
      return;
    }
    listSubdirs(sourcePath)
      .then((data) => { if (!cancelled) setSubdirs(data.subdirs); })
      .catch(() => { if (!cancelled) setSubdirs(null); });
    return () => { cancelled = true; };
  }, [sourcePath]);

  // After scan, use actual serials from sessions as ground truth
  const scannedSerials = scanResult?.sessions
    ?.map((s) => s.aircraft_serial)
    .filter((s) => s && s.trim()) ?? [];
  const uniqueScanned = [...new Set(scannedSerials)];

  const info = uniqueScanned.length > 0
    ? parseDirStructure(sourcePath, uniqueScanned)
    : parseDirStructure(sourcePath, subdirs);

  if (!info.message) return null;

  const colors = {
    ok: 'bg-green-50 text-green-700 border-green-200',
    warn: 'bg-amber-50 text-amber-700 border-amber-200',
    error: 'bg-red-50 text-red-600 border-red-200',
  };

  return (
    <div className={`mb-3 px-3 py-2 rounded-lg border text-xs ${colors[info.level]}`}>
      {info.level === 'ok' && '✓ '}
      {info.level === 'warn' && '⚠ '}
      {info.level === 'error' && '✗ '}
      {info.message}
    </div>
  );
}

// ── ImportPage ─────────────────────────────────────────────

export default function ImportPage({ onImported }: Props) {
  const [path, setPath] = useState('');
  const [scanning, setScanning] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResult | null>(null);

  // Model/Aircraft context
  const [models, setModels] = useState<AircraftModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null);
  const [aircraftList, setAircraftList] = useState<Aircraft[]>([]);
  const [sessionAircraftMap, setSessionAircraftMap] = useState<Record<string, number>>({});

  // Import states
  const [importingKeys, setImportingKeys] = useState<Set<string>>(new Set());
  const [importedKeys, setImportedKeys] = useState<Set<string>>(new Set());
  const [errorKeys, setErrorKeys] = useState<Record<string, string>>({});

  // Flight management
  const [flights, setFlights] = useState<Flight[]>([]);
  const [flightSearch, setFlightSearch] = useState('');
  const [editingFlightId, setEditingFlightId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [deletingFlightId, setDeletingFlightId] = useState<number | null>(null);

  // Create aircraft inline
  const [showCreateAircraft, setShowCreateAircraft] = useState<Record<string, boolean>>({});
  const [newAircraftSerial, setNewAircraftSerial] = useState('');

  function sessionKey(serial: string, skey: string) { return `${serial}__${skey}`; }

  // ─── Load context ────────────────────────────────────────

  const loadContext = async () => {
    try {
      const data = await listModels();
      setModels(data.models);
    } catch {}
  };

  const loadAircraftForModel = async (modelId: number) => {
    try {
      const data = await listAircraft(modelId);
      setAircraftList(data.aircraft);
    } catch {}
  };

  // ─── Browse / Scan ────────────────────────────────────────

  const doScan = async (scanPath: string) => {
    setScanning(true);
    setScanResult(null);
    await loadContext();
    try {
      const data = await scanFolder(scanPath);
      // Reload model list first if a new model was auto-created,
      // so the model is available before the dropdown renders
      if (data.model?.is_new) {
        await loadContext();
      }
      setScanResult(data);
      if (data.model) {
        setSelectedModelId(data.model.id);
        await loadAircraftForModel(data.model.id);
      } else {
        setSelectedModelId(null);
      }
      setImportingKeys(new Set());
      setImportedKeys(new Set());
      setErrorKeys({});
      setSessionAircraftMap({});
    } catch (e: any) {
      setScanResult({ source_path: scanPath, folder_name: scanPath, format_category: null, model: null, sessions: [], error: '扫描失败: ' + e.message });
    } finally { setScanning(false); }
  };

  const handleBrowse = async () => {
    setBrowsing(true);
    try {
      const data = await browseFolder();
      if (data.path && !data.cancelled) {
        setPath(data.path);
        await doScan(data.path);
      }
    } catch {} finally { setBrowsing(false); }
  };

  const handleScan = async () => {
    if (!path.trim()) return;
    await doScan(path.trim());
  };

  // ─── Import ───────────────────────────────────────────────

  const getAircraftId = (session: SessionPreview): number | null => {
    const key = sessionKey(session.aircraft_serial, session.session_key);
    if (sessionAircraftMap[key]) return sessionAircraftMap[key];
    if (session.aircraft_id) return session.aircraft_id;
    // Try to find matching aircraft from list
    const match = aircraftList.find((a) => a.serial_number === session.aircraft_serial);
    if (match) return match.id;
    return null;
  };

  const ensureAircraft = async (session: SessionPreview): Promise<number | null> => {
    // Already assigned
    let aid = getAircraftId(session);
    if (aid) return aid;

    // Need to create aircraft — requires a model and a serial
    if (!selectedModelId || !session.aircraft_serial) return null;

    // Check if aircraft already exists under this model
    const fresh = await listAircraft(selectedModelId);
    const match = fresh.aircraft.find((a) => a.serial_number === session.aircraft_serial);
    if (match) {
      setAircraftList(fresh.aircraft);
      return match.id;
    }

    // Auto-create aircraft from detected serial
    try {
      await createAircraft(selectedModelId, session.aircraft_serial.trim());
      const updated = await listAircraft(selectedModelId);
      setAircraftList(updated.aircraft);
      const created = updated.aircraft.find((a) => a.serial_number === session.aircraft_serial);
      return created ? created.id : null;
    } catch {
      return null;
    }
  };

  const refreshScan = async () => {
    if (!path.trim()) return;
    try { const data = await scanFolder(path.trim()); setScanResult(data); } catch {}
  };

  const handleImport = async (session: SessionPreview) => {
    const key = sessionKey(session.aircraft_serial, session.session_key);

    // Auto-create aircraft if needed
    const aid = await ensureAircraft(session);
    if (!aid) {
      setErrorKeys((prev) => ({ ...prev, [key]: selectedModelId ? '请先分配飞机（选择已有飞机或创建新飞机）' : '请先选择机型' }));
      return;
    }

    setImportingKeys((prev) => new Set(prev).add(key));
    setErrorKeys((prev) => { const n = { ...prev }; delete n[key]; return n; });

    try {
      const result = await importSession(path, aid, session.session_key);
      if (result.error) throw new Error(result.error);
      setImportedKeys((prev) => new Set(prev).add(key));
      onImported();
      loadFlights();
      refreshScan();
    } catch (e: any) {
      setErrorKeys((prev) => ({ ...prev, [key]: e.message }));
    } finally {
      setImportingKeys((prev) => { const n = new Set(prev); n.delete(key); return n; });
    }
  };

  // ─── Create model / aircraft ──────────────────────────────

  // Manual override: create a fresh model even when auto-match exists
  const handleCreateModelFromScan = async () => {
    if (!scanResult?.format_category || !path.trim()) return;
    const cat = scanResult.format_category;
    const modelName = `${cat}-${Date.now().toString(36)}`;
    try {
      const result = await createModelFromScan(modelName, path.trim(), cat);
      setSelectedModelId(result.id);
      await loadContext();
      await loadAircraftForModel(result.id);
    } catch (e: any) {
      alert('创建机型失败: ' + e.message);
    }
  };

  const handleCreateAircraft = async (serial: string, sessionKey?: string) => {
    if (!serial.trim() || !selectedModelId) return;
    try {
      await createAircraft(selectedModelId, serial.trim());
      setShowCreateAircraft({});
      setNewAircraftSerial('');
      const updated = await listAircraft(selectedModelId);
      setAircraftList(updated.aircraft);
      // Auto-assign to session if provided
      if (sessionKey) {
        const created = updated.aircraft.find((a) => a.serial_number === serial.trim());
        if (created) {
          setSessionAircraftMap((prev) => ({ ...prev, [sessionKey]: created.id }));
          setErrorKeys((prev) => { const n = { ...prev }; delete n[sessionKey]; return n; });
        }
      }
    } catch {}
  };

  // ─── Flight list management ───────────────────────────────

  const loadFlights = useCallback(async () => {
    try { const data = await listFlights(); setFlights(data.flights); } catch {}
  }, []);

  const handleDelete = async (id: number) => {
    await deleteFlight(id);
    setDeletingFlightId(null);
    loadFlights();
    onImported();
    refreshScan();
  };

  const handleRename = async (id: number) => {
    if (!editName.trim()) { setEditingFlightId(null); return; }
    await updateFlight(id, editName.trim());
    setEditingFlightId(null);
    loadFlights();
  };

  const startRename = (f: Flight) => { setEditingFlightId(f.id); setEditName(f.name); };

  const filteredFlights = flights.filter((f) => {
    if (!flightSearch.trim()) return true;
    const s = flightSearch.toLowerCase();
    return f.name.toLowerCase().includes(s)
      || (f.aircraft_serial || '').toLowerCase().includes(s)
      || (f.model_name || '').toLowerCase().includes(s);
  });

  // ─── Render data type badges ──────────────────────────────

  const renderBadges = (dataTypes: Record<string, number>) => (
    <div className="flex flex-wrap gap-1">
      {Object.entries(dataTypes).map(([type, count]) => (
        <span key={type} className="px-1.5 py-0.5 bg-gray-100 rounded text-xs text-gray-600">
          {DATA_TYPE_LABELS[type] || type} {count > 1 && `×${count}`}
        </span>
      ))}
    </div>
  );

  // ─── UI ───────────────────────────────────────────────────

  return (
    <div className="h-full overflow-auto p-8 max-w-4xl mx-auto space-y-8">
      {/* Section 1: Folder & Scan */}
      <section>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">导入飞行数据</h2>
        <div className="flex gap-3">
          <input type="text" value={path} onChange={(e) => setPath(e.target.value)}
            placeholder="输入飞行数据文件夹路径，或点击浏览选择"
            className="flex-1 bg-white border border-gray-300 rounded-lg px-4 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-blue-500" />
          <button onClick={handleBrowse} disabled={browsing}
            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 disabled:opacity-40 rounded-lg text-sm font-medium text-gray-700">
            {browsing ? '...' : '浏览'}
          </button>
          <button onClick={handleScan} disabled={scanning || !path.trim()}
            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 disabled:opacity-40 rounded-lg text-sm font-medium text-gray-700">
            {scanning ? '扫描中...' : scanResult ? '重新扫描' : '扫描'}
          </button>
        </div>
      </section>

      {/* Section 2: Scan Results */}
      {scanResult && (
        <section>
          <div className="flex items-center gap-3 mb-3">
            <h3 className="text-sm font-medium text-gray-500">
              扫描结果 — {scanResult.folder_name}
            </h3>
            {scanResult.format_category && (
              <span className={`px-2 py-0.5 rounded text-xs font-medium border ${formatBadgeStyle(scanResult.format_category)}`}>
                {scanResult.format_category}
              </span>
            )}
            {scanResult.format_detected && (
              <span className="text-xs text-green-600">✓ 自动检测</span>
            )}
          </div>

          {/* Directory structure validation */}
          <DirStructureBanner sourcePath={path} scanResult={scanResult} />

          {/* Model selection — always resolved (matched or auto-created) */}
          {scanResult.model && scanResult.sessions.length > 0 && (
            <div className="mb-4 p-3 bg-gray-50 rounded-lg border border-gray-200 space-y-2">
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-500">机型:</span>
                <select
                  value={selectedModelId ?? ''}
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    setSelectedModelId(id);
                    if (id) loadAircraftForModel(id);
                  }}
                  className="bg-white border border-gray-300 rounded px-3 py-1.5 text-sm text-gray-800 focus:outline-none focus:border-blue-500"
                >
                  {/* Always show resolved model first, then other models sharing the same format_category */}
                  <option value={scanResult.model.id}>
                    {scanResult.model.name} (推荐)
                  </option>
                  {models
                    .filter((m) => m.id !== scanResult.model!.id)
                    .map((m) => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                </select>
                {scanResult.model.is_new ? (
                  <span className="px-2 py-0.5 bg-green-100 text-green-700 rounded text-xs font-medium border border-green-200">
                    已自动创建
                  </span>
                ) : scanResult.model.match_confidence != null ? (
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-600 rounded text-xs font-medium border border-blue-200">
                    匹配度 {(scanResult.model.match_confidence * 100).toFixed(0)}%
                  </span>
                ) : null}
                <button
                  onClick={handleCreateModelFromScan}
                  className="px-2 py-1 text-xs text-blue-600 hover:text-blue-800 hover:bg-blue-50 rounded border border-dashed border-blue-300 hover:border-blue-400"
                >
                  + 新建机型
                </button>
              </div>

              {/* Aircraft assignment for sessions without matching aircraft */}
              {selectedModelId && scanResult.sessions.some((s) => !getAircraftId(s)) && (
                <div className="text-xs text-gray-500 pt-1">
                  部分场次未分配飞机，请在下方的每个场次中选择或创建飞机
                </div>
              )}
            </div>
          )}

          {scanResult.error && scanResult.sessions.length === 0 && (
            <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
              <p className="text-sm text-gray-400">{scanResult.error}</p>
            </div>
          )}

          {/* Sessions */}
          {scanResult.sessions.length > 0 && (
            <div className="space-y-3">
              {scanResult.sessions.map((session) => {
                const key = sessionKey(session.aircraft_serial, session.session_key);
                const isImporting = importingKeys.has(key);
                const isImported = importedKeys.has(key) || (session.import_status === 'imported' && !importingKeys.has(key));
                const errMsg = errorKeys[key];
                const aid = getAircraftId(session);

                return (
                  <div key={key}
                    className={`bg-white rounded-lg p-4 border transition-colors ${
                      errMsg ? 'border-red-200 bg-red-50/30' :
                      isImported ? 'border-green-200 bg-green-50/20' : 'border-gray-200'
                    }`}>
                    <div className="flex items-start justify-between gap-4">
                      <div className="space-y-2 min-w-0">
                        <div className="flex items-center gap-3 flex-wrap">
                          {/* Aircraft serial / assignment status */}
                          {session.aircraft_serial ? (
                            <span className={aid
                              ? 'px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-bold border border-blue-200'
                              : 'px-2 py-0.5 bg-amber-100 text-amber-700 rounded text-xs font-bold border border-amber-200'}>
                              {aid ? session.aircraft_serial : `${session.aircraft_serial}（将自动创建）`}
                            </span>
                          ) : aid ? (
                            <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-bold border border-blue-200">
                              {aircraftList.find((a) => a.id === aid)?.serial_number || aid}
                            </span>
                          ) : selectedModelId ? (
                            <span className="px-2 py-0.5 bg-red-50 text-red-500 rounded text-xs font-medium border border-red-200">
                              需要分配飞机
                            </span>
                          ) : (
                            <span className="px-2 py-0.5 bg-gray-100 text-gray-400 rounded text-xs border border-gray-200">
                              请先选择机型
                            </span>
                          )}
                          <span className="text-sm font-mono text-gray-700">{session.session_key || '(默认场次)'}</span>

                          {/* Aircraft assignment controls (shown when no aircraft assigned) */}
                          {!aid && selectedModelId && (
                            <div className="flex items-center gap-1">
                              <select
                                value=""
                                onChange={(e) => {
                                  const aId = Number(e.target.value);
                                  if (aId) {
                                    setSessionAircraftMap((prev) => ({ ...prev, [key]: aId }));
                                    setErrorKeys((prev) => { const n = { ...prev }; delete n[key]; return n; });
                                  }
                                }}
                                className="bg-white border border-gray-300 rounded px-2 py-0.5 text-xs"
                              >
                                <option value="">选择已有飞机...</option>
                                {aircraftList.map((a) => (
                                  <option key={a.id} value={a.id}>{a.serial_number}{a.name ? ` (${a.name})` : ''}</option>
                                ))}
                              </select>
                              {showCreateAircraft[key] ? (
                                <div className="flex items-center gap-1">
                                  <input type="text" value={newAircraftSerial}
                                    onChange={(e) => setNewAircraftSerial(e.target.value)}
                                    placeholder={session.aircraft_serial || "输入飞机序号"}
                                    className="bg-white border border-blue-400 rounded px-1 py-0.5 text-xs w-24 focus:outline-none"
                                    onKeyDown={(e) => { if (e.key === 'Enter') handleCreateAircraft(newAircraftSerial || session.aircraft_serial, key); }} />
                                  <button onClick={() => handleCreateAircraft(newAircraftSerial || session.aircraft_serial, key)}
                                    className="text-[10px] px-1.5 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">创建</button>
                                  <button onClick={() => setShowCreateAircraft((p) => ({ ...p, [key]: false }))}
                                    className="text-[10px] px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">取消</button>
                                </div>
                              ) : (
                                <button onClick={() => { setShowCreateAircraft((p) => ({ ...p, [key]: true })); setNewAircraftSerial(session.aircraft_serial || ''); }}
                                  className="text-[10px] text-blue-600 hover:text-blue-500 whitespace-nowrap">+ 新飞机</button>
                              )}
                            </div>
                          )}

                          {isImporting && <span className="text-xs text-blue-500 animate-pulse">⏳ 导入中...</span>}
                          {isImported && !isImporting && <span className="text-xs text-green-600 font-medium">✓ 已导入</span>}
                          {errMsg && <span className="text-xs text-red-500" title={errMsg}>✗ 失败</span>}
                          <span className="text-xs text-gray-400">{session.file_count} 个文件</span>
                          {session.import_status === 'imported' && session.existing_flight_name && (
                            <span className="text-[10px] text-gray-400">当前: {session.existing_flight_name}</span>
                          )}
                        </div>
                        {renderBadges(session.data_types)}
                        {errMsg && <p className="text-xs text-red-500">{errMsg}</p>}
                      </div>
                      <div className="shrink-0 flex items-center gap-2">
                        {!isImported && !isImporting && (
                          <button onClick={() => handleImport(session)} disabled={!selectedModelId && !aid}
                            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded text-xs font-medium"
                            title={!selectedModelId && !aid ? '请先选择机型' : '导入'}>
                            导入
                          </button>
                        )}
                        {isImporting && (
                          <button disabled
                            className="px-3 py-1 bg-gray-200 text-gray-400 rounded text-xs cursor-not-allowed">导入中...</button>
                        )}
                        {errMsg && (
                          <button onClick={() => handleImport(session)}
                            className="px-2 py-1 text-xs text-blue-600 hover:text-blue-500">重试</button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {/* Section 3: Imported Flights */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900">已导入飞行</h2>
          <div className="flex items-center gap-3">
            <input type="text" value={flightSearch} onChange={(e) => setFlightSearch(e.target.value)}
              placeholder="搜索架次..."
              className="bg-white border border-gray-300 rounded-lg px-3 py-1.5 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500 w-44" />
            <button onClick={loadFlights} className="text-xs text-blue-600 hover:text-blue-500">刷新</button>
          </div>
        </div>
        {filteredFlights.length === 0 && flights.length > 0 ? (
          <p className="text-sm text-gray-400">无匹配结果</p>
        ) : flights.length === 0 ? (
          <p className="text-sm text-gray-400">暂无已导入的飞行数据</p>
        ) : (
          <div className="space-y-2">
            {filteredFlights.map((f) => (
              <div key={f.id} className="flex items-center justify-between bg-white rounded-lg px-4 py-3 border border-gray-200">
                <div className="flex items-center gap-4">
                  <span className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px] font-medium">
                    {f.model_name || f.format_category}
                  </span>
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
                    {f.aircraft_serial || f.drone_id || '?'}
                  </span>
                  {editingFlightId === f.id ? (
                    <div className="flex items-center gap-1">
                      <input type="text" value={editName} onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleRename(f.id); if (e.key === 'Escape') setEditingFlightId(null); }}
                        className="bg-white border border-blue-400 rounded px-2 py-0.5 text-sm text-gray-800 focus:outline-none w-40" autoFocus />
                      <button onClick={() => handleRename(f.id)} className="text-xs px-2 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">保存</button>
                      <button onClick={() => setEditingFlightId(null)} className="text-xs px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">取消</button>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1 group">
                      <span className="text-sm font-medium text-gray-800">{f.name}</span>
                      <button onClick={() => startRename(f)}
                        className="text-gray-300 hover:text-blue-500 opacity-0 group-hover:opacity-100 transition-opacity text-xs">✏️</button>
                    </div>
                  )}
                  {f.session_key && <span className="text-xs text-gray-400 font-mono">{f.session_key}</span>}
                  {f.duration_sec && <span className="text-xs text-gray-400">{Math.round(f.duration_sec / 60)}分钟</span>}
                </div>
                {deletingFlightId === f.id ? (
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500">确认删除?</span>
                    <button onClick={() => handleDelete(f.id)} className="text-xs px-2 py-1 bg-red-600 text-white rounded hover:bg-red-500">是</button>
                    <button onClick={() => setDeletingFlightId(null)} className="text-xs px-2 py-1 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">否</button>
                  </div>
                ) : (
                  <button onClick={() => setDeletingFlightId(f.id)}
                    className="text-xs text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50">删除</button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
