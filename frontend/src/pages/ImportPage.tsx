import { useState, useCallback, useEffect } from 'react';
import {
  scanFolder, importSession, listFlights, deleteFlight, updateFlight, browseFolder,
  listModels, createModelFromScan, listAircraft, createAircraft, listSubdirs,
  getSyncExportTree, exportSyncPackage,
  type Flight, type ScanResult, type SessionPreview,
  type AircraftModel, type Aircraft, type FlightRecordFields,
  type SyncExportModelNode, type SyncExportResult,
} from '../api';


interface Props {
  onImported: () => void;
  canDeleteFlights: boolean;
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

function emptyRecord(): FlightRecordFields {
  return {
    record_daily_duration_min: null,
    record_batch_name: '',
    record_location: '',
    record_payload: '',
    record_weather: '',
    record_fuel_amount: null,
    record_takeoff_weight: null,
    record_altitude: null,
    record_wind_speed: null,
    record_note: '',
  };
}

function parseNumberInput(value: string): number | null {
  if (value.trim() === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function recordSummary(record: FlightRecordFields): string {
  const parts = [
    record.record_batch_name,
    record.record_location,
    record.record_weather,
    record.record_takeoff_weight != null ? `${record.record_takeoff_weight}kg` : '',
    record.record_payload,
  ].filter(Boolean);
  return parts.length ? parts.join(' / ') : '未填写';
}

function RecordInput({
  label,
  value,
  onChange,
  type = 'text',
}: {
  label: string;
  value: string | number | null | undefined;
  onChange: (value: string) => void;
  type?: 'text' | 'number';
}) {
  return (
    <label className="space-y-1">
      <span className="block text-[11px] text-gray-500">{label}</span>
      <input
        type={type}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-white border border-gray-300 rounded px-2 py-1 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
      />
    </label>
  );
}

// ── ImportPage ─────────────────────────────────────────────

export default function ImportPage({ onImported, canDeleteFlights }: Props) {
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
  const [expandedRecords, setExpandedRecords] = useState<Set<string>>(new Set());

  // Flight management
  const [flights, setFlights] = useState<Flight[]>([]);
  const [flightSearch, setFlightSearch] = useState('');
  const [editingFlightId, setEditingFlightId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [deletingFlightId, setDeletingFlightId] = useState<number | null>(null);

  // Offline sync export
  const [exportOpen, setExportOpen] = useState(false);
  const [exportFilter, setExportFilter] = useState('');
  const [exportTree, setExportTree] = useState<SyncExportModelNode[]>([]);
  const [selectedExportIds, setSelectedExportIds] = useState<Set<number>>(new Set());
  const [exporting, setExporting] = useState(false);
  const [exportLoading, setExportLoading] = useState(false);
  const [exportError, setExportError] = useState('');
  const [exportResult, setExportResult] = useState<SyncExportResult | null>(null);

  // Create aircraft inline
  const [showCreateAircraft, setShowCreateAircraft] = useState<Record<string, boolean>>({});
  const [newAircraftSerial, setNewAircraftSerial] = useState('');

  // New-format model creation (when no existing model matches the folder)
  const [newModelName, setNewModelName] = useState('');
  const [newModelTypes, setNewModelTypes] = useState<Set<string>>(new Set());
  const [creatingModel, setCreatingModel] = useState(false);

  function sessionKey(serial: string, skey: string) { return `${serial}__${skey}`; }

  // ─── Dynamic duplicate status ──────────────────────────────
  // Evaluates whether the session is a duplicate based on which
  // aircraft the user currently has selected. A session is only
  // "imported" if the SAME aircraft already has this date+time.

  type EffectiveStatus = 'new' | 'imported' | 'conflict';

  function getEffectiveStatus(session: SessionPreview, selectedAircraftSerial: string | null): EffectiveStatus {
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

  // When a scan finds no matching model, seed the new-model form: default name
  // from the scan, and pre-select every non-raw discovered type. is_raw types
  // (raw byte dumps like HandlePacket/AllReceivedData/SendCommand) default to
  // deselected but remain available for the user to opt in.
  useEffect(() => {
    if (scanResult && !scanResult.model && scanResult.discovered_types) {
      setNewModelName(scanResult.suggested_name ?? '');
      setNewModelTypes(
        new Set(scanResult.discovered_types.filter((t) => !t.is_raw).map((t) => t.data_type_key)),
      );
    }
  }, [scanResult]);

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
      setSessionRecords({});
      setExpandedRecords(new Set());
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

    // Auto-create aircraft if needed
    const aid = await ensureAircraft(session);
    if (!aid) {
      setErrorKeys((prev) => ({ ...prev, [key]: selectedModelId ? '请先分配飞机（选择已有飞机或创建新飞机）' : '请先选择机型' }));
      return;
    }

    setImportingKeys((prev) => new Set(prev).add(key));
    setErrorKeys((prev) => { const n = { ...prev }; delete n[key]; return n; });

    try {
      const result = await importSession(path, aid, session.session_key, sessionRecords[key] ?? emptyRecord());
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
    if (!path.trim()) return;
    const modelName = `新机型-${Date.now().toString(36)}`;
    try {
      const result = await createModelFromScan(modelName, path.trim());
      setSelectedModelId(result.id);
      await loadContext();
      await loadAircraftForModel(result.id);
    } catch (e: any) {
      alert('创建机型失败: ' + e.message);
    }
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

  const getSessionRecord = (key: string): FlightRecordFields => sessionRecords[key] ?? emptyRecord();

  const updateSessionRecord = (key: string, patch: Partial<FlightRecordFields>) => {
    setSessionRecords((prev) => ({
      ...prev,
      [key]: { ...emptyRecord(), ...(prev[key] ?? {}), ...patch },
    }));
  };

  const toggleRecordExpanded = (key: string) => {
    setExpandedRecords((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const visibleExportFlightIds = exportTree.flatMap((model) =>
    model.aircraft.flatMap((aircraft) =>
      aircraft.batches.flatMap((batch) => batch.flights.map((flight) => flight.id)),
    ),
  );

  const loadExportTree = useCallback(async (q = exportFilter) => {
    setExportLoading(true);
    setExportError('');
    try {
      const data = await getSyncExportTree(q);
      setExportTree(data.tree);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExportLoading(false);
    }
  }, [exportFilter]);

  useEffect(() => {
    if (!exportOpen) return;
    const timer = window.setTimeout(() => loadExportTree(exportFilter), 250);
    return () => window.clearTimeout(timer);
  }, [exportFilter, exportOpen, loadExportTree]);

  const openExportDialog = async () => {
    setExportOpen(true);
    setExportResult(null);
    setExportError('');
    await loadExportTree('');
  };

  const toggleExportFlight = (id: number) => {
    setSelectedExportIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectVisibleExportFlights = () => {
    setSelectedExportIds((prev) => {
      const next = new Set(prev);
      visibleExportFlightIds.forEach((id) => next.add(id));
      return next;
    });
  };

  const clearVisibleExportFlights = () => {
    setSelectedExportIds((prev) => {
      const next = new Set(prev);
      visibleExportFlightIds.forEach((id) => next.delete(id));
      return next;
    });
  };

  const submitExport = async () => {
    if (selectedExportIds.size === 0) return;
    setExporting(true);
    setExportError('');
    setExportResult(null);
    try {
      const result = await exportSyncPackage(Array.from(selectedExportIds));
      setExportResult(result);
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setExporting(false);
    }
  };

  const filteredFlights = flights.filter((f) => {
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

          {/* New format: no model matched → prompt the user to create one,
              choosing which discovered data types to keep. */}
          {!scanResult.model && scanResult.discovered_types && (
            <div className="mb-4 p-4 bg-amber-50 rounded-lg border border-amber-200 space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-amber-800">发现新格式</span>
                <span className="text-xs text-amber-600">未匹配到已有机型，创建新机型后即可导入</span>
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
                const effStatus = getEffectiveStatus(session, selectedSerial);
                const isImported = importedKeys.has(key)
                  || (effStatus === 'imported' && !importingKeys.has(key));
                const isConflict = effStatus === 'conflict';
                const errMsg = errorKeys[key];
                const record = getSessionRecord(key);
                const isRecordExpanded = expandedRecords.has(key);

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
                          {session.flight_date && (
                            <span className="text-xs text-gray-400">{session.flight_date}</span>
                          )}

                          {/* Aircraft assignment controls (always shown when model selected) */}
                          {selectedModelId && (
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
                        <div className="mt-3 rounded border border-gray-200 bg-gray-50 p-3 space-y-3">
                          <div className="flex items-center justify-between gap-3">
                            <div className="text-xs text-gray-500">
                              飞行记录: <span className="text-gray-700">{recordSummary(record)}</span>
                            </div>
                            <button
                              type="button"
                              onClick={() => toggleRecordExpanded(key)}
                              className="text-xs text-blue-600 hover:text-blue-500"
                            >
                              {isRecordExpanded ? '收起字段' : '展开全部'}
                            </button>
                          </div>
                          <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                            <RecordInput label="批次" value={record.record_batch_name} onChange={(v) => updateSessionRecord(key, { record_batch_name: v })} />
                            <RecordInput label="地点" value={record.record_location} onChange={(v) => updateSessionRecord(key, { record_location: v })} />
                            <RecordInput label="天气" value={record.record_weather} onChange={(v) => updateSessionRecord(key, { record_weather: v })} />
                            <RecordInput label="起飞重量（kg）" type="number" value={record.record_takeoff_weight} onChange={(v) => updateSessionRecord(key, { record_takeoff_weight: parseNumberInput(v) })} />
                            <RecordInput label="备注" value={record.record_note} onChange={(v) => updateSessionRecord(key, { record_note: v })} />
                          </div>
                          {isRecordExpanded && (
                            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                              <RecordInput label="单日飞行时长（分钟）" type="number" value={record.record_daily_duration_min} onChange={(v) => updateSessionRecord(key, { record_daily_duration_min: parseNumberInput(v) })} />
                              <RecordInput label="设备载荷" value={record.record_payload} onChange={(v) => updateSessionRecord(key, { record_payload: v })} />
                              <RecordInput label="燃油量（kg）" type="number" value={record.record_fuel_amount} onChange={(v) => updateSessionRecord(key, { record_fuel_amount: parseNumberInput(v) })} />
                              <RecordInput label="海拔高度（m）" type="number" value={record.record_altitude} onChange={(v) => updateSessionRecord(key, { record_altitude: parseNumberInput(v) })} />
                              <RecordInput label="风速（m/s）" type="number" value={record.record_wind_speed} onChange={(v) => updateSessionRecord(key, { record_wind_speed: parseNumberInput(v) })} />
                            </div>
                          )}
                        </div>
                        {errMsg && <p className="text-xs text-red-500">{errMsg}</p>}
                      </div>
                      <div className="shrink-0 flex items-center gap-2">
                        {!isImported && !isImporting && (
                          <button onClick={() => handleImport(session)} disabled={!selectedModelId && !aid}
                            className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded text-xs font-medium"
                            title={isConflict ? '该日期+时间已有其他飞机导入，如确认为不同飞机则可导入' : (!selectedModelId && !aid ? '请先选择机型' : '导入')}>
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
            <button
              onClick={openExportDialog}
              className="px-3 py-1.5 text-xs bg-blue-600 text-white rounded-lg hover:bg-blue-500"
            >
              导出同步包
            </button>
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
                  {(f.raw_warnings?.length ?? 0) > 0 && (
                    <span className="text-xs text-amber-600">warning {f.raw_warnings!.length}</span>
                  )}
                </div>
                {canDeleteFlights && deletingFlightId === f.id ? (
                  <div className="flex items-center gap-1">
                    <span className="text-xs text-gray-500">确认删除?</span>
                    <button onClick={() => handleDelete(f.id)} className="text-xs px-2 py-1 bg-red-600 text-white rounded hover:bg-red-500">是</button>
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

      {exportOpen && (
        <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-6">
          <div className="w-full max-w-4xl max-h-[86vh] bg-white rounded-lg shadow-xl border border-gray-200 flex flex-col">
            <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between gap-4">
              <div>
                <div className="text-base font-semibold text-gray-900">导出离线同步包</div>
                <div className="text-xs text-gray-500 mt-1">
                  已选择 {selectedExportIds.size} 个架次，包将保存到固定 sync_exports 目录
                </div>
              </div>
              <button
                onClick={() => setExportOpen(false)}
                className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded"
              >
                关闭
              </button>
            </div>
            <div className="px-5 py-3 border-b border-gray-100 flex items-center gap-2">
              <input
                value={exportFilter}
                onChange={(e) => setExportFilter(e.target.value)}
                placeholder="筛选机型、飞机、批次、架次、日期、地点、天气"
                className="flex-1 bg-white border border-gray-300 rounded px-3 py-1.5 text-sm text-gray-700 focus:outline-none focus:border-blue-500"
              />
              <button
                onClick={selectVisibleExportFlights}
                disabled={visibleExportFlightIds.length === 0}
                className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40"
              >
                全选当前结果
              </button>
              <button
                onClick={clearVisibleExportFlights}
                disabled={visibleExportFlightIds.length === 0}
                className="px-3 py-1.5 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 disabled:opacity-40"
              >
                清除当前结果
              </button>
            </div>
            <div className="flex-1 overflow-auto px-5 py-4 space-y-4">
              {exportLoading ? (
                <div className="text-sm text-gray-400">加载中...</div>
              ) : exportTree.length === 0 ? (
                <div className="text-sm text-gray-400">无可导出的架次</div>
              ) : (
                exportTree.map((model) => (
                  <div key={model.id} className="space-y-2">
                    <div className="text-sm font-semibold text-gray-800">{model.name}</div>
                    {model.aircraft.map((aircraft) => (
                      <div key={aircraft.id} className="ml-3 border-l border-gray-200 pl-3 space-y-2">
                        <div className="text-xs font-medium text-blue-700">{aircraft.name}</div>
                        {aircraft.batches.map((batch) => (
                          <div key={batch.name} className="ml-3 space-y-1">
                            <div className="text-xs text-gray-500">{batch.name}</div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-1">
                              {batch.flights.map((flight) => (
                                <label
                                  key={flight.id}
                                  className="flex items-center gap-2 rounded border border-gray-200 px-3 py-2 text-xs hover:bg-gray-50 cursor-pointer"
                                >
                                  <input
                                    type="checkbox"
                                    checked={selectedExportIds.has(flight.id)}
                                    onChange={() => toggleExportFlight(flight.id)}
                                  />
                                  <span className="font-medium text-gray-800">{flight.name}</span>
                                  {flight.flight_date && <span className="text-gray-400">{flight.flight_date}</span>}
                                  {flight.session_key && <span className="font-mono text-gray-400">{flight.session_key}</span>}
                                  {flight.record_location && <span className="text-gray-400">{flight.record_location}</span>}
                                </label>
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                ))
              )}
              {exportError && (
                <div className="text-xs text-red-600 bg-red-50 border border-red-100 rounded px-3 py-2">
                  {exportError}
                </div>
              )}
              {exportResult && (
                <div className="text-xs text-green-700 bg-green-50 border border-green-100 rounded px-3 py-2 space-y-1">
                  <div>导出完成: {exportResult.filename}</div>
                  <div className="font-mono break-all">{exportResult.path}</div>
                  <div>架次 {exportResult.flight_count}，原始文件 {exportResult.raw_file_count}</div>
                </div>
              )}
            </div>
            <div className="px-5 py-4 border-t border-gray-200 flex items-center justify-end gap-2">
              <button
                onClick={() => setExportOpen(false)}
                className="px-3 py-1.5 text-sm bg-gray-100 text-gray-700 rounded hover:bg-gray-200"
              >
                取消
              </button>
              <button
                onClick={submitExport}
                disabled={exporting || selectedExportIds.size === 0}
                className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-500 disabled:opacity-40"
              >
                {exporting ? '导出中...' : '导出'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
