import { useState } from 'react';
import { deleteFlight, updateFlight, type Flight } from '../../api/flights';
import {
  SYNC_STATE_FILTERS,
  deleteActionLabel,
  deleteScopeFor,
  matchesSyncStateFilter,
  syncStateClass,
  syncStateLabel,
  type SyncStateFilter,
} from '../../syncStatus';

interface Props {
  flights: Flight[];
  canDeleteFlights: boolean;
  serverOnline: boolean;
  onRefresh: () => void | Promise<void>;
  onDeleted: () => void | Promise<void>;
}

export default function ImportedFlightList({ flights, canDeleteFlights, serverOnline, onRefresh, onDeleted }: Props) {
  const [search, setSearch] = useState('');
  const [syncFilter, setSyncFilter] = useState<SyncStateFilter>('all');
  const [editingFlightId, setEditingFlightId] = useState<number | null>(null);
  const [editName, setEditName] = useState('');
  const [deletingFlightId, setDeletingFlightId] = useState<number | null>(null);

  const visibleFlights = flights.filter((flight) => {
    if (!matchesSyncStateFilter(flight, syncFilter)) return false;
    if (!search.trim()) return true;
    const needle = search.toLowerCase();
    return flight.name.toLowerCase().includes(needle)
      || (flight.aircraft_name || '').toLowerCase().includes(needle)
      || (flight.model_name || '').toLowerCase().includes(needle);
  });

  const startRename = (flight: Flight) => {
    setEditingFlightId(flight.id);
    setEditName(flight.name);
  };

  const rename = async (flightId: number) => {
    if (!editName.trim()) {
      setEditingFlightId(null);
      return;
    }
    await updateFlight(flightId, editName.trim());
    setEditingFlightId(null);
    await onRefresh();
  };

  const remove = async (flight: Flight) => {
    await deleteFlight(flight.id, deleteScopeFor(flight, serverOnline));
    setDeletingFlightId(null);
    await onDeleted();
  };

  return (
    <section>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900">已导入飞行</h2>
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索架次..."
            className="bg-white border border-gray-300 rounded-lg px-3 py-1.5 text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-blue-500 w-44"
          />
          <select
            value={syncFilter}
            onChange={(event) => setSyncFilter(event.target.value as SyncStateFilter)}
            className="bg-white border border-gray-300 rounded-lg px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus:border-blue-500"
          >
            {SYNC_STATE_FILTERS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
          </select>
          <button onClick={onRefresh} className="text-xs text-blue-600 hover:text-blue-500">刷新</button>
        </div>
      </div>
      {visibleFlights.length === 0 && flights.length > 0 ? (
        <p className="text-sm text-gray-400">无匹配结果</p>
      ) : flights.length === 0 ? (
        <p className="text-sm text-gray-400">暂无已导入的飞行数据</p>
      ) : (
        <div className="space-y-2">
          {visibleFlights.map((flight) => (
            <div key={flight.id} className="flex items-center justify-between bg-white rounded-lg px-4 py-3 border border-gray-200">
              <div className="flex items-center gap-4">
                <span className="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-[10px] font-medium">{flight.model_name}</span>
                <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">{flight.aircraft_name || flight.drone_id || '?'}</span>
                {editingFlightId === flight.id ? (
                  <div className="flex items-center gap-1">
                    <input
                      type="text"
                      value={editName}
                      onChange={(event) => setEditName(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter') rename(flight.id);
                        if (event.key === 'Escape') setEditingFlightId(null);
                      }}
                      className="bg-white border border-blue-400 rounded px-2 py-0.5 text-sm text-gray-800 focus:outline-none w-40"
                      autoFocus
                    />
                    <button onClick={() => rename(flight.id)} className="text-xs px-2 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">保存</button>
                    <button onClick={() => setEditingFlightId(null)} className="text-xs px-2 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">取消</button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1 group">
                    <span className="text-sm font-medium text-gray-800">{flight.name}</span>
                    <button onClick={() => startRename(flight)} className="text-gray-300 hover:text-blue-500 opacity-0 group-hover:opacity-100 transition-opacity text-xs">✏️</button>
                  </div>
                )}
                {flight.session_key && <span className="text-xs text-gray-400 font-mono">{flight.session_key}</span>}
                {flight.duration_sec && <span className="text-xs text-gray-400">{Math.round(flight.duration_sec / 60)}分钟</span>}
                <span className="text-xs text-gray-400">原始文件 {flight.raw_file_count ?? 0}</span>
                <span className={`text-[10px] px-2 py-0.5 rounded border ${syncStateClass(flight.sync_state)}`}>{syncStateLabel(flight.sync_state)}</span>
                {(flight.raw_warnings?.length ?? 0) > 0 && <span className="text-xs text-amber-600">warning {flight.raw_warnings!.length}</span>}
              </div>
              {canDeleteFlights && deletingFlightId === flight.id ? (
                <div className="flex items-center gap-1">
                  <span className="text-xs text-gray-500">{deleteActionLabel(flight, serverOnline)}?</span>
                  <button onClick={() => remove(flight)} className="text-xs px-2 py-1 bg-red-600 text-white rounded hover:bg-red-500">是</button>
                  <button onClick={() => setDeletingFlightId(null)} className="text-xs px-2 py-1 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">否</button>
                </div>
              ) : canDeleteFlights ? (
                <button onClick={() => setDeletingFlightId(flight.id)} className="text-xs text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50">删除</button>
              ) : (
                <span className="text-xs text-gray-300 px-2 py-1" title="当前环境或登录状态无删除权限">删除</span>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
