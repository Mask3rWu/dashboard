import { useState, useEffect } from 'react';
import {
  listModels, updateModel, deleteModel,
  listAircraft, createAircraft, updateAircraft, deleteAircraft,
  deleteFlight, updateFlight,
  type AircraftModel, type Aircraft, type Flight,
} from '../api';

interface Props {
  onModelsChanged: () => void;
  onNavigateToFlight: (flightId: number) => void;
  flights: Flight[];
  modelsVersion: number;
}

export default function ModelManager({ onModelsChanged, onNavigateToFlight, flights, modelsVersion }: Props) {
  const [models, setModels] = useState<AircraftModel[]>([]);
  const [selectedModelId, setSelectedModelId] = useState<number | null>(null);
  const [aircraft, setAircraft] = useState<Aircraft[]>([]);

  // Edit model
  const [editingModelId, setEditingModelId] = useState<number | null>(null);
  const [editModelName, setEditModelName] = useState('');

  // Delete model
  const [deletingModelId, setDeletingModelId] = useState<number | null>(null);

  // Create aircraft
  const [showAddAircraft, setShowAddAircraft] = useState(false);
  const [newSerial, setNewSerial] = useState('');

  // Edit aircraft serial
  const [editingAcId, setEditingAcId] = useState<number | null>(null);
  const [editAcSerial, setEditAcSerial] = useState('');

  // Delete aircraft
  const [deletingAcId, setDeletingAcId] = useState<number | null>(null);

  // Expanded aircraft (show flights)
  const [expandedAc, setExpandedAc] = useState<Set<number>>(new Set());

  // Flight editing
  const [editingFlightId, setEditingFlightId] = useState<number | null>(null);
  const [editFlightName, setEditFlightName] = useState('');
  const [deletingFlightId, setDeletingFlightId] = useState<number | null>(null);

  const loadModels = async () => {
    try {
      const data = await listModels();
      setModels(data.models);
    } catch (e) { console.error(e); }
  };

  const loadAircraft = async (modelId: number) => {
    try {
      const data = await listAircraft(modelId);
      setAircraft(data.aircraft);
    } catch (e) { console.error(e); }
  };

  useEffect(() => { loadModels(); }, []);

  // Refresh models/aircraft when external data changes (e.g. import on another tab)
  useEffect(() => {
    if (modelsVersion > 0) {
      loadModels();
      if (selectedModelId) loadAircraft(selectedModelId);
    }
  }, [modelsVersion]);

  useEffect(() => {
    if (selectedModelId) loadAircraft(selectedModelId);
    else setAircraft([]);
  }, [selectedModelId]);

  const refresh = () => {
    loadModels();
    if (selectedModelId) loadAircraft(selectedModelId);
    onModelsChanged();
  };

  const handleRenameModel = async (id: number) => {
    if (!editModelName.trim()) { setEditingModelId(null); return; }
    await updateModel(id, editModelName.trim());
    setEditingModelId(null);
    loadModels();
    onModelsChanged();  // refresh flight list so dropdown labels update
  };

  const handleDeleteModel = async (id: number) => {
    await deleteModel(id);
    setDeletingModelId(null);
    if (selectedModelId === id) setSelectedModelId(null);
    refresh();
  };

  const handleAddAircraft = async () => {
    if (!newSerial.trim() || !selectedModelId) return;
    await createAircraft(selectedModelId, newSerial.trim());
    setShowAddAircraft(false);
    setNewSerial('');
    loadAircraft(selectedModelId);
    loadModels();
    onModelsChanged();
  };

  const handleRenameAircraft = async (id: number) => {
    if (!editAcSerial.trim()) { setEditingAcId(null); return; }
    await updateAircraft(id, editAcSerial.trim());
    setEditingAcId(null);
    if (selectedModelId) loadAircraft(selectedModelId);
    refresh();
  };

  const handleDeleteAircraft = async (id: number) => {
    await deleteAircraft(id);
    setDeletingAcId(null);
    if (selectedModelId) loadAircraft(selectedModelId);
    refresh();
  };

  const handleRenameFlight = async (id: number) => {
    if (!editFlightName.trim()) { setEditingFlightId(null); return; }
    await updateFlight(id, editFlightName.trim());
    setEditingFlightId(null);
    refresh();
  };

  const handleDeleteFlight = async (id: number) => {
    await deleteFlight(id);
    setDeletingFlightId(null);
    refresh();
  };

  const toggleExpand = (acId: number) => {
    setExpandedAc((prev) => {
      const next = new Set(prev);
      if (next.has(acId)) next.delete(acId);
      else next.add(acId);
      return next;
    });
  };

  const selectedModel = models.find((m) => m.id === selectedModelId);

  const getFlightsForAircraft = (acId: number): Flight[] =>
    flights.filter((f) => f.aircraft_id === acId);

  return (
    <div className="h-full flex">
      {/* Left: Model List */}
      <aside className="w-64 shrink-0 border-r border-gray-200 overflow-y-auto bg-gray-50/50 flex flex-col">
        <div className="p-3 border-b border-gray-200">
          <span className="text-xs font-medium text-gray-500">机型列表</span>
        </div>

        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {models.map((m) => (
            <div key={m.id}>
              <div
                onClick={() => setSelectedModelId(m.id)}
                className={`rounded-lg px-3 py-2 cursor-pointer transition-colors ${
                  selectedModelId === m.id
                    ? 'bg-blue-50 border border-blue-200'
                    : 'bg-white border border-gray-200 hover:bg-gray-100'
                }`}
              >
                {editingModelId === m.id ? (
                  <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="text" value={editModelName}
                      onChange={(e) => setEditModelName(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') handleRenameModel(m.id); if (e.key === 'Escape') setEditingModelId(null); }}
                      className="flex-1 bg-white border border-blue-400 rounded px-1 py-0.5 text-xs focus:outline-none"
                      autoFocus
                    />
                    <button onClick={() => handleRenameModel(m.id)} className="text-[10px] text-blue-600 px-1">✓</button>
                    <button onClick={() => setEditingModelId(null)} className="text-[10px] text-gray-400 px-1">✕</button>
                  </div>
                ) : (
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-800 truncate">{m.name}</span>
                    <div className="flex items-center gap-0.5 shrink-0 ml-1">
                      <button
                        onClick={(e) => { e.stopPropagation(); setEditingModelId(m.id); setEditModelName(m.name); }}
                        className="text-gray-300 hover:text-blue-500 p-0.5"
                        title="重命名"
                      >
                        ✏️
                      </button>
                      {deletingModelId === m.id ? (
                        <span className="text-[10px] text-red-500 whitespace-nowrap">
                          确认?{' '}
                          <button onClick={(e) => { e.stopPropagation(); handleDeleteModel(m.id); }} className="text-red-600 font-bold">是</button>
                          {' / '}
                          <button onClick={(e) => { e.stopPropagation(); setDeletingModelId(null); }} className="text-gray-400">否</button>
                        </span>
                      ) : (
                        <button
                          onClick={(e) => { e.stopPropagation(); setDeletingModelId(m.id); }}
                          className="text-gray-300 hover:text-red-500 p-0.5"
                          title="删除"
                        >
                          🗑
                        </button>
                      )}
                    </div>
                  </div>
                )}
                <div className="text-[10px] text-gray-400 mt-0.5">
                  {(m.aircraft_count ?? 0)} 架飞机
                </div>
              </div>
            </div>
          ))}
          {models.length === 0 && (
            <p className="text-xs text-gray-400 p-2">暂无机型</p>
          )}
        </div>
      </aside>

      {/* Right: Aircraft & Flights */}
      <main className="flex-1 overflow-y-auto p-6">
        {!selectedModel ? (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            选择一个机型查看其飞机列表
          </div>
        ) : (
          <div className="max-w-3xl">
            <div className="flex items-center justify-between mb-6">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">{selectedModel.name}</h2>
              </div>
              <button
                onClick={() => setShowAddAircraft(true)}
                className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-500"
              >
                + 添加飞机
              </button>
            </div>

            {/* Add aircraft form */}
            {showAddAircraft && (
              <div className="mb-4 p-3 bg-white border border-gray-200 rounded-lg space-y-2">
                <input
                  type="text" value={newSerial}
                  onChange={(e) => setNewSerial(e.target.value)}
                  placeholder="飞机序号 (如 21)"
                  className="w-full bg-white border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:border-blue-500"
                  onKeyDown={(e) => e.key === 'Enter' && handleAddAircraft()}
                />
                <div className="flex gap-1">
                  <button onClick={handleAddAircraft} className="text-xs px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-500">添加</button>
                  <button onClick={() => setShowAddAircraft(false)} className="text-xs px-3 py-1 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">取消</button>
                </div>
              </div>
            )}

            {/* Aircraft list with expandable flights */}
            {aircraft.length === 0 ? (
              <p className="text-sm text-gray-400">暂无飞机，请添加飞机序号</p>
            ) : (
              <div className="space-y-2">
                {aircraft.map((ac) => {
                  const acFlights = getFlightsForAircraft(ac.id);
                  const isExpanded = expandedAc.has(ac.id);

                  return (
                    <div key={ac.id} className="bg-white border border-gray-200 rounded-lg overflow-hidden">
                      {/* Aircraft row */}
                      <div
                        className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-gray-50 transition-colors"
                        onClick={() => toggleExpand(ac.id)}
                      >
                        <div className="flex items-center gap-3">
                          <span className="text-xs text-gray-400 transition-transform"
                            style={{ transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}>
                            ▶
                          </span>
                          <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
                            {ac.serial_number}
                          </span>
                          {editingAcId === ac.id ? (
                            <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                              <input
                                type="text" value={editAcSerial}
                                onChange={(e) => setEditAcSerial(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter') handleRenameAircraft(ac.id);
                                  if (e.key === 'Escape') setEditingAcId(null);
                                }}
                                className="bg-white border border-blue-400 rounded px-2 py-0.5 text-sm focus:outline-none w-24"
                                autoFocus
                              />
                              <button onClick={() => handleRenameAircraft(ac.id)} className="text-xs px-2 py-0.5 bg-blue-600 text-white rounded">保存</button>
                              <button onClick={() => setEditingAcId(null)} className="text-xs px-2 py-0.5 bg-gray-200 text-gray-600 rounded">取消</button>
                            </div>
                          ) : null}
                          <span className="text-xs text-gray-400">
                            {acFlights.length} 个架次
                          </span>
                        </div>
                        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                          <button
                            onClick={() => { setEditingAcId(ac.id); setEditAcSerial(ac.serial_number); }}
                            className="text-xs text-gray-400 hover:text-blue-500"
                          >
                            重命名
                          </button>
                          {deletingAcId === ac.id ? (
                            <span className="text-xs text-gray-500">
                              确认?{' '}
                              <button onClick={() => handleDeleteAircraft(ac.id)} className="text-red-600 font-bold">是</button>
                              {' / '}
                              <button onClick={() => setDeletingAcId(null)} className="text-gray-400">否</button>
                            </span>
                          ) : (
                            <button
                              onClick={() => setDeletingAcId(ac.id)}
                              className="text-xs text-red-400 hover:text-red-600"
                            >
                              删除
                            </button>
                          )}
                        </div>
                      </div>

                      {/* Expandable flights sub-list */}
                      {isExpanded && (
                        <div className="border-t border-gray-100 bg-gray-50/50">
                          {acFlights.length === 0 ? (
                            <p className="text-xs text-gray-400 px-6 py-3">暂无飞行架次</p>
                          ) : (
                            acFlights.map((f) => (
                              <div key={f.id} className="flex items-center justify-between px-6 py-2 border-b border-gray-100 last:border-b-0 hover:bg-white transition-colors">
                                <div className="flex items-center gap-3 min-w-0">
                                  <span className="text-sm font-medium text-gray-700 truncate max-w-[200px]">
                                    {editingFlightId === f.id ? (
                                      <div className="flex items-center gap-1">
                                        <input
                                          type="text" value={editFlightName}
                                          onChange={(e) => setEditFlightName(e.target.value)}
                                          onKeyDown={(e) => {
                                            if (e.key === 'Enter') handleRenameFlight(f.id);
                                            if (e.key === 'Escape') setEditingFlightId(null);
                                          }}
                                          className="bg-white border border-blue-400 rounded px-2 py-0.5 text-xs focus:outline-none w-36"
                                          autoFocus
                                        />
                                        <button onClick={() => handleRenameFlight(f.id)} className="text-[10px] px-1.5 py-0.5 bg-blue-600 text-white rounded">✓</button>
                                        <button onClick={() => setEditingFlightId(null)} className="text-[10px] px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded">✕</button>
                                      </div>
                                    ) : (
                                      <span className="flex items-center gap-1 group">
                                        {f.name}
                                        <button
                                          onClick={() => { setEditingFlightId(f.id); setEditFlightName(f.name); }}
                                          className="text-gray-300 hover:text-blue-500 opacity-0 group-hover:opacity-100 text-[10px]"
                                        >✏️</button>
                                      </span>
                                    )}
                                  </span>
                                  <span className="text-xs text-gray-400 font-mono">{f.session_key}</span>
                                  {f.duration_sec != null && (
                                    <span className="text-xs text-gray-400">{Math.round(f.duration_sec / 60)}分钟</span>
                                  )}
                                  <span className="text-xs text-gray-400">
                                    {f.start_time && `${f.start_time}${f.end_time ? ` ~ ${f.end_time.split(' ').pop()}` : ''}`}
                                  </span>
                                </div>
                                <div className="flex items-center gap-2 shrink-0">
                                  <button
                                    onClick={() => onNavigateToFlight(f.id)}
                                    className="text-xs text-blue-600 hover:text-blue-500 font-medium"
                                  >
                                    分析 →
                                  </button>
                                  {deletingFlightId === f.id ? (
                                    <span className="text-xs text-gray-500">
                                      确认?{' '}
                                      <button onClick={() => handleDeleteFlight(f.id)} className="text-red-600 font-bold">是</button>
                                      {' / '}
                                      <button onClick={() => setDeletingFlightId(null)} className="text-gray-400">否</button>
                                    </span>
                                  ) : (
                                    <button
                                      onClick={() => setDeletingFlightId(f.id)}
                                      className="text-xs text-red-400 hover:text-red-600"
                                    >
                                      删除
                                    </button>
                                  )}
                                </div>
                              </div>
                            ))
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
