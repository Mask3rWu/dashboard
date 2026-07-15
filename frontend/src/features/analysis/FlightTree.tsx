import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Pencil, Trash2 } from 'lucide-react';
import type { Flight } from '../../api/flights';
import { listAircraft, type Aircraft, type AircraftModel } from '../../api/models';
import { syncStateClass, syncStateLabel } from '../../syncStatus';

interface Props {
  flights: Flight[];
  models: AircraftModel[];
  aircraft: Aircraft[];
  selectedFlightId: number | null;
  selectedModelId: number | null;
  selectedAircraftId: number | null;
  search: string;
  editingFlightId: number | null;
  deletingFlightId: number | null;
  canDeleteFlights: boolean;
  onSearchChange: (value: string) => void;
  onSelectFlight: (flightId: number) => void;
  onSelectModel: (modelId: number) => void;
  onSelectAircraft: (aircraftId: number) => void;
  onStartRename: (flight: Flight) => void;
  onRequestDelete: (flightId: number) => void;
}

export default function FlightTree({
  flights,
  models,
  aircraft,
  selectedFlightId,
  selectedModelId,
  selectedAircraftId,
  search,
  editingFlightId,
  deletingFlightId,
  canDeleteFlights,
  onSearchChange,
  onSelectFlight,
  onSelectModel,
  onSelectAircraft,
  onStartRename,
  onRequestDelete,
}: Props) {
  const [open, setOpen] = useState(false);
  const [treeModelId, setTreeModelId] = useState<number | null>(null);
  const [treeAircraftId, setTreeAircraftId] = useState<number | null>(null);
  const [treeAircraftList, setTreeAircraftList] = useState<Aircraft[]>([]);
  const treeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onMouseDown = (event: MouseEvent) => {
      if (treeRef.current && !treeRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [open]);

  const openModel = async (modelId: number) => {
    setTreeModelId(modelId);
    setTreeAircraftId(null);
    try {
      const data = await listAircraft(modelId);
      setTreeAircraftList(data.aircraft);
    } catch {
      setTreeAircraftList([]);
    }
  };

  const matchedFlightIds = (() => {
    if (!search.trim()) return null;
    const needle = search.toLowerCase();
    return new Set(flights.filter((flight) => (
      flight.name.toLowerCase().includes(needle)
      || (flight.aircraft_name || flight.drone_id || '').toLowerCase().includes(needle)
    )).map((flight) => flight.id));
  })();

  const visibleModels = matchedFlightIds
    ? models.filter((model) => flights.some((flight) => flight.model_id === model.id && matchedFlightIds.has(flight.id)))
    : models;
  const visibleAircraft = matchedFlightIds
    ? treeAircraftList.filter((item) => flights.some((flight) => flight.aircraft_id === item.id && matchedFlightIds.has(flight.id)))
    : treeAircraftList;
  const visibleFlights = (treeAircraftId
    ? flights.filter((flight) => flight.aircraft_id === treeAircraftId)
    : []).filter((flight) => {
      if (!search.trim()) return true;
      const needle = search.toLowerCase();
      return flight.name.toLowerCase().includes(needle)
        || (flight.aircraft_name || flight.drone_id || '').toLowerCase().includes(needle);
    });

  const selectFlight = (flightId: number) => {
    const flight = flights.find((item) => item.id === flightId);
    if (flight) {
      onSelectModel(flight.model_id);
      onSelectAircraft(flight.aircraft_id);
    }
    onSelectFlight(flightId);
    setOpen(false);
  };

  const selectedLabel = (() => {
    if (!selectedFlightId) return null;
    const flight = flights.find((item) => item.id === selectedFlightId);
    const model = models.find((item) => item.id === selectedModelId);
    const selectedAircraft = aircraft.find((item) => item.id === selectedAircraftId);
    if (flight && model && selectedAircraft) return `${model.name} / ${selectedAircraft.name} / ${flight.name}`;
    return flight?.name || '选择架次...';
  })();

  return (
    <div className="flex items-center gap-2" ref={treeRef}>
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 bg-white border border-gray-300 rounded-lg pl-3 pr-2 py-1.5 text-sm hover:border-blue-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 min-w-[180px] max-w-[360px]"
      >
        {selectedLabel ? <span className="text-gray-700 truncate">{selectedLabel}</span> : <span className="text-gray-400">选择架次...</span>}
        <ChevronDown className={`w-4 h-4 text-gray-400 ml-auto shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="absolute top-full left-4 mt-1 z-50 flex bg-white border border-gray-200 rounded-lg shadow-lg max-h-[320px]">
          <div className="w-44 border-r border-gray-100 overflow-y-auto py-1">
            <div className="px-3 py-1.5 text-xs text-gray-400 font-medium sticky top-0 bg-white">机型</div>
            {visibleModels.length === 0 ? <div className="px-3 py-2 text-xs text-gray-400">无匹配机型</div> : visibleModels.map((model) => (
              <button
                key={model.id}
                onMouseEnter={() => openModel(model.id)}
                className={`w-full text-left px-3 py-1.5 text-sm flex items-center justify-between ${treeModelId === model.id ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50'}`}
              >
                <span className="truncate">{model.name}</span>
                <ChevronRight className="w-3.5 h-3.5 text-gray-300 shrink-0" />
              </button>
            ))}
          </div>

          {treeModelId && (
            <div className="w-44 border-r border-gray-100 overflow-y-auto py-1">
              <div className="px-3 py-1.5 text-xs text-gray-400 font-medium sticky top-0 bg-white">飞机</div>
              {visibleAircraft.length === 0 ? <div className="px-3 py-2 text-xs text-gray-400">无匹配飞机</div> : visibleAircraft.map((item) => (
                <button
                  key={item.id}
                  onMouseEnter={() => setTreeAircraftId(item.id)}
                  className={`w-full text-left px-3 py-1.5 text-sm flex items-center justify-between ${treeAircraftId === item.id ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50'}`}
                >
                  <span className="truncate">{item.name}</span>
                  <ChevronRight className="w-3.5 h-3.5 text-gray-300 shrink-0" />
                </button>
              ))}
            </div>
          )}

          {treeAircraftId && (
            <div className="w-52 overflow-y-auto py-1">
              <div className="px-3 py-1.5 text-xs text-gray-400 font-medium sticky top-0 bg-white">架次</div>
              {visibleFlights.length === 0 ? <div className="px-3 py-2 text-xs text-gray-400">无架次</div> : visibleFlights.map((flight) => (
                <button
                  key={flight.id}
                  onClick={() => selectFlight(flight.id)}
                  className={`w-full text-left px-3 py-1.5 text-sm ${flight.id === selectedFlightId ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50'}`}
                >
                  <span className="flex items-center gap-2 min-w-0">
                    <span className="truncate">{flight.name}</span>
                    <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border ${syncStateClass(flight.sync_state)}`}>{syncStateLabel(flight.sync_state)}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <input
        type="text"
        value={search}
        onChange={(event) => onSearchChange(event.target.value)}
        placeholder="搜索架次..."
        className="bg-white border border-gray-300 rounded-lg px-2 py-1.5 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500 w-32"
      />

      {selectedFlightId && editingFlightId !== selectedFlightId && (
        <button
          onClick={() => {
            const flight = flights.find((item) => item.id === selectedFlightId);
            if (flight) onStartRename(flight);
          }}
          className="text-gray-400 hover:text-blue-500 text-xs px-1.5 py-1 rounded hover:bg-gray-100 shrink-0"
          title="重命名"
        >
          <Pencil className="w-4 h-4" />
        </button>
      )}

      {selectedFlightId && canDeleteFlights && deletingFlightId !== selectedFlightId && (
        <button
          onClick={() => onRequestDelete(selectedFlightId)}
          className="text-gray-400 hover:text-red-500 px-1.5 py-1 rounded hover:bg-red-50 shrink-0 flex items-center"
          title="删除"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}
