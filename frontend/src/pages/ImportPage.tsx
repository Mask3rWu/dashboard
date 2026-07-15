import { useState, useCallback, useEffect } from 'react';
import { scanFolder, importSession, browseFolder, type ScanResult, type SessionPreview } from '../api/imports';
import { listFlights, type Flight, type FlightRecordFields } from '../api/flights';
import { listModels, createModelFromScan, listAircraft, createAircraft, type AircraftModel, type Aircraft } from '../api/models';
import { emptyRecord } from '../features/flights/recordFields';
import DirectorySummary from '../features/import/DirectorySummary';
import DirectoryPicker from '../features/import/DirectoryPicker';
import ModelFromScanForm from '../features/import/ModelFromScanForm';
import ImportedFlightList from '../features/import/ImportedFlightList';
import SessionImportList from '../features/import/SessionImportList';


interface Props {
  onImported: () => void | Promise<void>;
  canDeleteFlights: boolean;
  serverOnline?: boolean;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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

  // Imported flights
  const [flights, setFlights] = useState<Flight[]>([]);

  // New-format model creation, including a manual override of a recommended match.
  const [newModelName, setNewModelName] = useState('');
  const [newModelTypes, setNewModelTypes] = useState<Set<string>>(new Set());
  const [creatingModel, setCreatingModel] = useState(false);
  const [showNewModelForm, setShowNewModelForm] = useState(false);

  function sessionKey(serial: string, skey: string) { return `${serial}__${skey}`; }

  // ─── Load context ────────────────────────────────────────

  const loadContext = async () => {
    try {
      const data = await listModels();
      setModels(data.models);
    } catch {
      // Keep the previous model list when the context refresh fails.
    }
  };

  const loadAircraftForModel = async (modelId: number) => {
    try {
      const data = await listAircraft(modelId);
      setAircraftList(data.aircraft);
    } catch {
      // Keep the previous aircraft list when the refresh fails.
    }
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
    } catch (error: unknown) {
      setScanResult({ source_path: scanPath, folder_name: scanPath, model: null, sessions: [], error: '扫描失败: ' + errorMessage(error) });
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
    } catch {
      // A cancelled or unavailable native folder dialog leaves the current path unchanged.
    } finally { setBrowsing(false); }
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
    const aid = getAircraftId(session);
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
    try { const data = await scanFolder(path.trim()); setScanResult(data); } catch {
      // Preserve the current scan preview when a background refresh fails.
    }
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
    } catch (error: unknown) {
      setErrorKeys((prev) => ({ ...prev, [key]: errorMessage(error) }));
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
    } catch (error: unknown) {
      alert('创建机型失败: ' + errorMessage(error));
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
      return true;
    } catch {
      // Inline creation errors are surfaced by leaving the assignment unresolved.
      return false;
    }
  };

  // ─── Flight list management ───────────────────────────────

  const loadFlights = useCallback(async () => {
    try { const data = await listFlights(); setFlights(data.flights); } catch {
      // Keep the current flight list when a background refresh fails.
    }
  }, []);

  // Load imported flights on mount so the "已导入飞行" list shows existing
  // records immediately, without requiring a new import to trigger the fetch.
  useEffect(() => {
    loadFlights();
  }, [loadFlights]);

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

  // ─── UI ───────────────────────────────────────────────────

  return (
    <div className="h-full overflow-auto p-8 max-w-4xl mx-auto space-y-8">
      <DirectoryPicker
        path={path}
        browsing={browsing}
        scanning={scanning}
        hasScanResult={!!scanResult}
        onPathChange={setPath}
        onBrowse={handleBrowse}
        onScan={handleScan}
      />

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

          {showNewModelForm && scanResult.discovered_types && (
            <ModelFromScanForm
              discoveredTypes={scanResult.discovered_types}
              hasMatchedModel={!!scanResult.model}
              name={newModelName}
              selectedTypes={newModelTypes}
              creating={creatingModel}
              onNameChange={setNewModelName}
              onSelectAll={() => setNewModelTypes(new Set(scanResult.discovered_types!.map((type) => type.data_type_key)))}
              onToggleType={toggleNewModelType}
              onSubmit={handleConfirmCreateModel}
              onCancel={() => setShowNewModelForm(false)}
            />
          )}
          {scanResult.sessions.length > 0 && (
            <SessionImportList
              sessions={scanResult.sessions}
              selectedModelId={selectedModelId}
              aircraft={aircraftList}
              aircraftAssignments={sessionAircraftMap}
              importingKeys={importingKeys}
              importedKeys={importedKeys}
              errors={errorKeys}
              records={sessionRecords}
              dates={sessionDates}
              onAssignAircraft={(key, aircraftId) => {
                setSessionAircraftMap((previous) => {
                  const next = { ...previous };
                  if (aircraftId) next[key] = aircraftId;
                  else delete next[key];
                  return next;
                });
                setErrorKeys((previous) => {
                  const next = { ...previous };
                  delete next[key];
                  return next;
                });
              }}
              onDateChange={updateSessionDate}
              onRecordChange={updateSessionRecord}
              onCreateAircraft={handleCreateAircraft}
              onImport={handleImport}
            />
          )}
        </section>
      )}

      <ImportedFlightList
        flights={flights}
        canDeleteFlights={canDeleteFlights}
        serverOnline={serverOnline}
        onRefresh={loadFlights}
        onDeleted={async () => {
          await loadFlights();
          await onImported();
          await refreshScan();
        }}
      />

    </div>
  );
}
