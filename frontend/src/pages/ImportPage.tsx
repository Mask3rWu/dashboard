import { useState, useCallback, useEffect } from 'react';
import { scanFolder, importSession, browseFolder, type ScanResult, type SessionPreview } from '../api/imports';
import { listFlights, deleteFlight, updateFlight, type Flight, type FlightRecordFields } from '../api/flights';
import { listModels, createModelFromScan, listAircraft, createAircraft, type AircraftModel, type Aircraft, type DeleteScope } from '../api/models';
import {
  SYNC_STATE_FILTERS,
  deleteActionLabel,
  deleteScopeFor,
  matchesSyncStateFilter,
  syncStateClass,
  syncStateLabel,
  type SyncStateFilter,
} from '../syncStatus';
import FlightRecordForm from '../features/flights/FlightRecordForm';
import { emptyRecord } from '../features/flights/recordFields';
import DirectorySummary from '../features/import/DirectorySummary';


interface Props {
  onImported: () => void | Promise<void>;
  canDeleteFlights: boolean;
  serverOnline?: boolean;
}

// ── Directory structure validation ─────────────────────────

// ── ImportPage ─────────────────────────────────────────────

export default function ImportPage({ onImported, canDeleteFlights, serverOnline = true }: Props) {
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
  const [sessionRecords, setSessionRecords] = useState<Record<string, FlightRecordFields>>({});
  const [sessionDates, setSessionDates] = useState<Record<string, string>>({});

  // Flight management
  const [flights, setFlights] = useState<Flight[]>([]);
  const [flightSearch, setFlightSearch] = useState('');
  const [syncFilter, setSyncFilter] = useState<SyncStateFilter>('all');
  const [editingFlightId, setEditingFlightId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [deletingFlightId, setDeletingFlightId] = useState<number | null>(null);

  // Create aircraft inline
  const [showCreateAircraft, setShowCreateAircraft] = useState<Record<string, boolean>>({});
  const [newAircraftSerial, setNewAircraftSerial] = useState('');

  // New-format model creation, including a manual override of a recommended match.
  const [newModelName, setNewModelName] = useState('');
  const [newModelTypes, setNewModelTypes] = useState<Set<string>>(new Set());
  const [creatingModel, setCreatingModel] = useState(false);
  const [showNewModelForm, setShowNewModelForm] = useState(false);

  function sessionKey(serial: string, skey: string) { return `${serial}__${skey}`; }

  // ─── Dynamic duplicate status ──────────────────────────────
  // Evaluates whether the session is a duplicate based on which
  // aircraft the user currently has selected. A session is only
  // "imported" if the SAME aircraft already has this date+time.

  type EffectiveStatus = 'new' | 'imported' | 'conflict';

  function getEffectiveStatus(session: SessionPreview, selectedAircraftSerial: string | null, flightDate: string): EffectiveStatus {
    if (session.flight_date && flightDate && flightDate !== session.flight_date) return 'new';

    // Backend already confirmed: auto-detected serial matches an imported flight
    if (session.import_status === 'imported') return 'imported';

    // No conflicts at all → clean new session
    if (!session.conflicting_aircraft?.length) return 'new';

    // Has conflicts: check if the user-selected aircraft is the conflicting one
    if (selectedAircraftSerial) {
      const match = session.conflicting_aircraft.find(
        c => c.aircraft_serial === selectedAircraftSerial
      );
      if (match) return 'imported';  // same aircraft → duplicate
    }

    // Different aircraft (or no aircraft selected yet) → new, but warn about conflict
    return 'conflict';
  }

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

  // Seed the new-model form from the scan, and pre-select every non-raw,
  // non-alert discovered type.
  // Raw byte dumps and alerts default to deselected but remain available for
  // the user to opt in.
  useEffect(() => {
    if (scanResult?.discovered_types) {
      setNewModelName(scanResult.suggested_name ?? '');
      setNewModelTypes(
        new Set(scanResult.discovered_types.filter((t) => !t.is_raw && !t.is_alert).map((t) => t.data_type_key)),
      );
      setShowNewModelForm(!scanResult.model);
    } else {
      setShowNewModelForm(false);
    }
  }, [scanResult]);

  // ─── Browse / Scan ────────────────────────────────────────

  const recordDefaultsBySession = (sessions: SessionPreview[]): Record<string, FlightRecordFields> => {
    const defaults: Record<string, FlightRecordFields> = {};
    sessions.forEach((session) => {
      if (session.record_defaults) {
        defaults[sessionKey(session.aircraft_serial, session.session_key)] = {
          ...emptyRecord(),
          ...session.record_defaults,
        };
      }
    });
    return defaults;
  };

  const doScan = async (scanPath: string) => {
    setScanning(true);
    setScanResult(null);
    await loadContext();
    try {
      const data = await scanFolder(scanPath);
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
      setSessionRecords(recordDefaultsBySession(data.sessions));
      setSessionDates({});
    } catch (e: any) {
      setScanResult({ source_path: scanPath, folder_name: scanPath, model: null, sessions: [], error: '扫描失败: ' + e.message });
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
    const match = aircraftList.find((a) => a.name === session.aircraft_serial);
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
    const match = fresh.aircraft.find((a) => a.name === session.aircraft_serial);
    if (match) {
      setAircraftList(fresh.aircraft);
      return match.id;
    }

    // Auto-create aircraft from detected serial
    try {
      await createAircraft(selectedModelId, session.aircraft_serial.trim());
      const updated = await listAircraft(selectedModelId);
      setAircraftList(updated.aircraft);
      const created = updated.aircraft.find((a) => a.name === session.aircraft_serial);
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
    const flightDate = getSessionDate(session);
    if (!flightDate) {
      setErrorKeys((prev) => ({ ...prev, [key]: '请先填写飞行日期' }));
      return;
    }

    // Auto-create aircraft if needed
    const aid = await ensureAircraft(session);
    if (!aid) {
      setErrorKeys((prev) => ({ ...prev, [key]: selectedModelId ? '请先分配飞机（选择已有飞机或创建新飞机）' : '请先选择机型' }));
      return;
    }

    setImportingKeys((prev) => new Set(prev).add(key));
    setErrorKeys((prev) => { const n = { ...prev }; delete n[key]; return n; });

    try {
      const result = await importSession(path, aid, session.session_key, {
        ...(sessionRecords[key] ?? emptyRecord()),
        flight_date: flightDate,
      });
      if (result.error) throw new Error(result.error);
      setImportedKeys((prev) => new Set(prev).add(key));
      await onImported();
      await loadFlights();
      await refreshScan();
    } catch (e: any) {
      setErrorKeys((prev) => ({ ...prev, [key]: e.message }));
    } finally {
      setImportingKeys((prev) => { const n = new Set(prev); n.delete(key); return n; });
    }
  };

  // ─── Create model / aircraft ──────────────────────────────

  const openNewModelForm = () => {
    if (!scanResult?.discovered_types) return;
    setNewModelName(scanResult.suggested_name ?? '');
    setNewModelTypes(
      new Set(scanResult.discovered_types.filter((t) => !t.is_raw && !t.is_alert).map((t) => t.data_type_key)),
    );
    setShowNewModelForm(true);
  };

  // New-format flow: create a model from the scan with the user's chosen name
  // and selected data types, then re-scan so the new model is matched.
  const handleConfirmCreateModel = async () => {
    if (!path.trim() || !newModelName.trim() || newModelTypes.size === 0) return;
    setCreatingModel(true);
    try {
      const result = await createModelFromScan(
        newModelName.trim(),
        path.trim(),
        Array.from(newModelTypes),
      );
      setSelectedModelId(result.id);
      await loadContext();
      await loadAircraftForModel(result.id);
      await refreshScan();
      setShowNewModelForm(false);
    } catch (e: any) {
      alert('创建机型失败: ' + e.message);
    } finally {
      setCreatingModel(false);
    }
  };

  const toggleNewModelType = (key: string) => {
    setNewModelTypes((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
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
        const created = updated.aircraft.find((a) => a.name === serial.trim());
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

  // Load imported flights on mount so the "已导入飞行" list shows existing
  // records immediately, without requiring a new import to trigger the fetch.
  useEffect(() => {
    loadFlights();
  }, [loadFlights]);

  const handleDelete = async (flight: Flight) => {
    await deleteFlight(flight.id, deleteScopeFor(flight, serverOnline) as DeleteScope);
    setDeletingFlightId(null);
    await loadFlights();
    await onImported();
    await refreshScan();
  };

  const handleRename = async (id: number) => {
    if (!editName.trim()) { setEditingFlightId(null); return; }
    await updateFlight(id, editName.trim());
    setEditingFlightId(null);
    loadFlights();
  };

  const startRename = (f: Flight) => { setEditingFlightId(f.id); setEditName(f.name); };

  const getSessionRecord = (key: string): FlightRecordFields => sessionRecords[key] ?? emptyRecord();

  const getSessionDate = (session: SessionPreview): string => {
    const key = sessionKey(session.aircraft_serial, session.session_key);
    return sessionDates[key] ?? session.flight_date ?? '';
  };

  const updateSessionDate = (key: string, value: string) => {
    setSessionDates((prev) => ({ ...prev, [key]: value }));
    setErrorKeys((prev) => { const n = { ...prev }; delete n[key]; return n; });
  };

  const updateSessionRecord = (key: string, patch: Partial<FlightRecordFields>) => {
    setSessionRecords((prev) => ({
      ...prev,
      [key]: { ...emptyRecord(), ...(prev[key] ?? {}), ...patch },
    }));
  };

  const filteredFlights = flights.filter((f) => {
    if (!matchesSyncStateFilter(f, syncFilter)) return false;
    if (!flightSearch.trim()) return true;
    const s = flightSearch.toLowerCase();
    return f.name.toLowerCase().includes(s)
      || (f.aircraft_name || '').toLowerCase().includes(s)
      || (f.model_name || '').toLowerCase().includes(s);
  });

  // ─── Render data type badges ──────────────────────────────

  const renderBadges = (dataTypes: Record<string, number>) => (
    <div className="flex flex-wrap gap-1">
      {Object.entries(dataTypes).map(([type, count]) => (
        <span key={type} className="px-1.5 py-0.5 bg-gray-100 rounded text-xs text-gray-600">
          {type} {count > 1 && `×${count}`}
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
            {scanResult.format_detected && (
              <span className="text-xs text-green-600">✓ 自动检测</span>
            )}
          </div>

          {/* Directory structure validation */}
          <DirectorySummary sourcePath={path} scanResult={scanResult} />

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
                  {/* Resolved model first, then other models */}
                  <option value={scanResult.model.id}>
                    {scanResult.model.name} (推荐)
                  </option>
                  {models
                    .filter((m) => m.id !== scanResult.model!.id)
                    .map((m) => (
                      <option key={m.id} value={m.id}>{m.name}</option>
                    ))}
                </select>
                {(() => {
                  const resolved = scanResult.model!;
                  const isResolvedSelected = selectedModelId === resolved.id;
                  // Find score for the currently selected model from the full score list.
                  // Falls back to resolved.match_confidence when selection IS the resolved one
                  // (covers the case where matching_models is missing/empty).
                  const selectedScore = isResolvedSelected
                    ? resolved.match_confidence
                    : scanResult.matching_models?.find((m) => m.id === selectedModelId)?.score ?? null;

                  if (isResolvedSelected && resolved.is_new) {
                    return (
                      <span className="px-2 py-0.5 bg-green-100 text-green-700 rounded text-xs font-medium border border-green-200">
                        已自动创建
                      </span>
                    );
                  }
                  if (selectedScore != null) {
                    return (
                      <span className="px-2 py-0.5 bg-blue-100 text-blue-600 rounded text-xs font-medium border border-blue-200"
                        title={isResolvedSelected ? '自动匹配的最佳机型' : '手动选择的机型与扫描结果的相似度'}>
                        匹配度 {(selectedScore * 100).toFixed(0)}%
                      </span>
                    );
                  }
                  return null;
                })()}
                <button
                  onClick={openNewModelForm}
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

          {/* New format, or a manual override of the recommended model. */}
          {showNewModelForm && scanResult.discovered_types && (
            <div className="mb-4 p-4 bg-amber-50 rounded-lg border border-amber-200 space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-amber-800">{scanResult.model ? '新建机型' : '发现新格式'}</span>
                <span className="text-xs text-amber-600">
                  {scanResult.model ? '不使用当前推荐机型，按扫描结果创建新机型' : '未匹配到已有机型，创建新机型后即可导入'}
                </span>
              </div>
              <div className="flex items-center gap-3 flex-wrap">
                <span className="text-xs text-gray-500">机型名称</span>
                <input
                  value={newModelName}
                  onChange={(e) => setNewModelName(e.target.value)}
                  className="bg-white border border-gray-300 rounded px-3 py-1.5 text-sm text-gray-800 focus:outline-none focus:border-blue-500"
                  placeholder="给新机型命名"
                />
              </div>
              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-gray-500">
                    数据类型（勾选要导入的，共 {scanResult.discovered_types.length} 个）
                  </span>
                  <button
                    onClick={() => setNewModelTypes(new Set(scanResult.discovered_types!.map((t) => t.data_type_key)))}
                    className="text-xs text-blue-600 hover:underline"
                  >
                    全选
                  </button>
                </div>
                {scanResult.discovered_types.map((t) => {
                  const checked = newModelTypes.has(t.data_type_key);
                  return (
                    <label
                      key={t.data_type_key}
                      className="flex items-center gap-2 text-sm py-1 px-2 rounded hover:bg-white/60 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleNewModelType(t.data_type_key)}
                      />
                      <span className="text-gray-800">{t.display_label}</span>
                      <span className="text-xs text-gray-400">{t.data_type_key}</span>
                      {t.is_alert && (
                        <span className="px-1.5 py-0.5 bg-amber-100 text-amber-700 rounded text-xs border border-amber-200">告警</span>
                      )}
                      {t.is_raw && (
                        <span
                          className="px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded text-xs border border-gray-300"
                          title="疑似原始字节转储，分析价值低，默认不导入。可手动勾选。"
                        >
                          原始数据
                        </span>
                      )}
                      <span className="text-xs text-gray-400 ml-auto">{t.column_count} 列</span>
                    </label>
                  );
                })}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleConfirmCreateModel}
                  disabled={creatingModel || !newModelName.trim() || newModelTypes.size === 0}
                  className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                >
                  {creatingModel ? '创建中…' : '创建机型并继续'}
                </button>
                {scanResult.model && (
                  <button
                    onClick={() => setShowNewModelForm(false)}
                    disabled={creatingModel}
                    className="px-3 py-1.5 text-sm bg-white text-gray-600 border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-50"
                  >
                    取消
                  </button>
                )}
                {newModelTypes.size === 0 && (
                  <span className="text-xs text-red-500">至少选择一个数据类型</span>
                )}
              </div>
            </div>
          )}

          {/* Sessions */}
          {scanResult.sessions.length > 0 && (
            <div className="space-y-3">
              {scanResult.sessions.map((session) => {
                const key = sessionKey(session.aircraft_serial, session.session_key);
                const isImporting = importingKeys.has(key);
                const aid = getAircraftId(session);
                const selectedSerial = aid
                  ? (aircraftList.find(a => a.id === aid)?.name ?? session.aircraft_serial)
                  : session.aircraft_serial;
                const flightDate = getSessionDate(session);
                const effStatus = getEffectiveStatus(session, selectedSerial, flightDate);
                const isImported = importedKeys.has(key)
                  || (effStatus === 'imported' && !importingKeys.has(key));
                const isConflict = effStatus === 'conflict';
                const errMsg = errorKeys[key];
                const record = getSessionRecord(key);

                // Card border based on effective status
                const cardBorder = errMsg
                  ? 'border-red-200 bg-red-50/30'
                  : isImported
                    ? 'border-green-200 bg-green-50/20'
                    : isConflict
                      ? 'border-amber-200 bg-amber-50/10'
                      : 'border-gray-200';

                return (
                  <div key={key}
                    className={`bg-white rounded-lg p-4 border transition-colors ${cardBorder}`}>
                    <div className="flex items-start justify-between gap-4">
                      <div className="space-y-2 min-w-0">
                        <div className="flex items-center gap-3 flex-wrap">
                          {/* Aircraft serial / assignment status */}
                          {aid ? (
                            <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-bold border border-blue-200">
                              {selectedSerial}
                            </span>
                          ) : session.aircraft_serial ? (
                            <span className="px-2 py-0.5 bg-amber-100 text-amber-700 rounded text-xs font-bold border border-amber-200">
                              {session.aircraft_serial}（将自动创建）
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
                          {/* Session key + flight date */}
                          <span className="text-sm font-mono text-gray-700">{session.session_key || '(默认场次)'}</span>
                          {!isImported && (
                            <label className="flex items-center gap-1 text-xs text-gray-500">
                              <span>时间</span>
                              <input
                                type="date"
                                required
                                value={flightDate}
                                onChange={(e) => updateSessionDate(key, e.target.value)}
                                className={`bg-white border rounded px-1.5 py-0.5 text-xs text-gray-700 focus:outline-none focus:border-blue-500 ${flightDate ? 'border-gray-300' : 'border-red-300'}`}
                              />
                            </label>
                          )}

                          {/* Aircraft assignment controls (always shown when model selected) */}
                          {selectedModelId && !isImported && (
                            <div className="flex items-center gap-1">
                              <select
                                value={aid ?? ''}
                                onChange={(e) => {
                                  const aId = e.target.value ? Number(e.target.value) : null;
                                  if (aId) {
                                    setSessionAircraftMap((prev) => ({ ...prev, [key]: aId }));
                                  } else {
                                    // Revert to auto-detected — remove manual assignment
                                    setSessionAircraftMap((prev) => {
                                      const n = { ...prev };
                                      delete n[key];
                                      return n;
                                    });
                                  }
                                  setErrorKeys((prev) => { const n = { ...prev }; delete n[key]; return n; });
                                }}
                                className="bg-white border border-gray-300 rounded px-1.5 py-0.5 text-xs"
                              >
                                <option value="">选择已有飞机...</option>
                                {aircraftList.map((a) => (
                                  <option key={a.id} value={a.id}>{a.name}</option>
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
                          {isConflict && !isImported && !isImporting && (
                            <span className="text-xs text-amber-600 font-medium">⚠ 存在冲突</span>
                          )}
                          {errMsg && <span className="text-xs text-red-500" title={errMsg}>✗ 失败</span>}
                          <span className="text-xs text-gray-400">{session.file_count} 个文件</span>
                          {session.record_defaults && (
                            <span className="text-xs text-emerald-600" title={session.record_source || 'FlightRecord XML'}>XML预填</span>
                          )}
                          {session.record_defaults_error && (
                            <span className="text-xs text-red-500" title={session.record_defaults_error}>XML错误</span>
                          )}
                          {effStatus === 'imported' && session.existing_flight_name && (
                            <span className="text-[10px] text-gray-400">当前: {session.existing_flight_name}</span>
                          )}
                        </div>
                        {/* Conflict warning — different aircraft already has this date+time */}
                        {isConflict && session.conflicting_aircraft && (
                          <div className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded px-2 py-1">
                            ⚠ 飞机「{session.conflicting_aircraft.map(c => c.aircraft_serial).join('、')}」已导入此日期+时间的飞行。
                            如当前确认为不同飞机，可继续导入。
                          </div>
                        )}
                        {renderBadges(session.data_types)}
                        {!isImported && (
                          <div className="mt-3 rounded border border-gray-200 bg-gray-50 p-3 space-y-3">
                            <FlightRecordForm
                              value={record}
                              onChange={(patch) => updateSessionRecord(key, patch)}
                              variant="import"
                            />
                          </div>
                        )}
                        {errMsg && <p className="text-xs text-red-500">{errMsg}</p>}
                      </div>
                      <div className="shrink-0 flex items-center gap-2">
                        {!isImported && !isImporting && (
                          <button onClick={() => handleImport(session)} disabled={(!selectedModelId && !aid) || !flightDate}
                            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded text-xs font-medium"
                            title={!flightDate ? '请先填写飞行日期' : (isConflict ? '该日期+时间已有其他飞机导入，如确认为不同飞机则可导入' : (!selectedModelId && !aid ? '请先选择机型' : '导入'))}>
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
            <select
              value={syncFilter}
              onChange={(e) => setSyncFilter(e.target.value as SyncStateFilter)}
              className="bg-white border border-gray-300 rounded-lg px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
            >
              {SYNC_STATE_FILTERS.map((item) => (
                <option key={item.key} value={item.key}>{item.label}</option>
              ))}
            </select>
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
                    {f.model_name}
                  </span>
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
                    {f.aircraft_name || f.drone_id || '?'}
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
                  <span className="text-xs text-gray-400">原始文件 {f.raw_file_count ?? 0}</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded border ${syncStateClass(f.sync_state)}`}>
                    {syncStateLabel(f.sync_state)}
                  </span>
                  {(f.raw_warnings?.length ?? 0) > 0 && (
                    <span className="text-xs text-amber-600">warning {f.raw_warnings!.length}</span>
                  )}
                </div>
                {canDeleteFlights && deletingFlightId === f.id ? (
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500">{deleteActionLabel(f, serverOnline)}?</span>
                    <button onClick={() => handleDelete(f)} className="text-xs px-2 py-1 bg-red-600 text-white rounded hover:bg-red-500">是</button>
                    <button onClick={() => setDeletingFlightId(null)} className="text-xs px-2 py-1 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">否</button>
                  </div>
                ) : canDeleteFlights ? (
                  <button onClick={() => setDeletingFlightId(f.id)}
                    className="text-xs text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50">删除</button>
                ) : (
                  <span className="text-xs text-gray-300 px-2 py-1" title="当前环境或登录状态无删除权限">删除</span>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

    </div>
  );
}
