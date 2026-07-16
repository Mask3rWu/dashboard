import { useState } from 'react';
import type { FlightRecordFields } from '../../api/flights';
import type { SessionPreview } from '../../api/imports';
import type { Aircraft } from '../../api/models';
import FlightRecordForm from '../flights/FlightRecordForm';
import { emptyRecord } from '../flights/recordFields';

interface Props {
  sessions: SessionPreview[];
  selectedModelId: number | null;
  aircraft: Aircraft[];
  aircraftAssignments: Record<string, number>;
  importingKeys: Set<string>;
  importedKeys: Set<string>;
  errors: Record<string, string>;
  records: Record<string, FlightRecordFields>;
  dates: Record<string, string>;
  onAssignAircraft: (key: string, aircraftId: number | null) => void;
  onDateChange: (key: string, value: string) => void;
  onRecordChange: (key: string, patch: Partial<FlightRecordFields>) => void;
  onCreateAircraft: (serial: string, key: string) => boolean | void | Promise<boolean | void>;
  onImport: (session: SessionPreview) => void | Promise<void>;
}

type EffectiveStatus = 'new' | 'imported' | 'conflict';

function sessionKey(serial: string, key: string) {
  return `${serial}__${key}`;
}

function effectiveStatus(session: SessionPreview, selectedAircraftSerial: string | null, flightDate: string): EffectiveStatus {
  if (session.flight_date && flightDate && flightDate !== session.flight_date) return 'new';
  if (session.import_status === 'imported') return 'imported';
  if (!session.conflicting_aircraft?.length) return 'new';
  if (selectedAircraftSerial && session.conflicting_aircraft.some((item) => item.aircraft_serial === selectedAircraftSerial)) return 'imported';
  return 'conflict';
}

function DataTypeBadges({ dataTypes }: { dataTypes: Record<string, number> }) {
  return (
    <div className="flex flex-wrap gap-1">
      {Object.entries(dataTypes).map(([type, count]) => (
        <span key={type} className="px-1.5 py-0.5 bg-gray-100 rounded text-xs text-gray-600">{type} {count > 1 && `×${count}`}</span>
      ))}
    </div>
  );
}

export default function SessionImportList({
  sessions,
  selectedModelId,
  aircraft,
  aircraftAssignments,
  importingKeys,
  importedKeys,
  errors,
  records,
  dates,
  onAssignAircraft,
  onDateChange,
  onRecordChange,
  onCreateAircraft,
  onImport,
}: Props) {
  const [showCreateAircraft, setShowCreateAircraft] = useState<Record<string, boolean>>({});
  const [newAircraftSerial, setNewAircraftSerial] = useState('');

  const createAircraft = async (serial: string, key: string) => {
    const created = await onCreateAircraft(serial, key);
    if (created !== false) {
      setShowCreateAircraft({});
      setNewAircraftSerial('');
    }
  };

  return (
    <div className="space-y-3">
      {sessions.map((session) => {
        const key = sessionKey(session.aircraft_serial, session.session_key);
        const isImporting = importingKeys.has(key);
        const aircraftId = aircraftAssignments[key]
          || session.aircraft_id
          || aircraft.find((item) => item.name === session.aircraft_serial)?.id
          || null;
        const selectedSerial = aircraftId
          ? (aircraft.find((item) => item.id === aircraftId)?.name ?? session.aircraft_serial)
          : session.aircraft_serial;
        const flightDate = dates[key] ?? session.flight_date ?? '';
        const status = effectiveStatus(session, selectedSerial, flightDate);
        const isImported = importedKeys.has(key) || (status === 'imported' && !isImporting);
        const isConflict = status === 'conflict';
        const error = errors[key];
        const record = records[key] ?? emptyRecord();
        const cardBorder = error
          ? 'border-red-200 bg-red-50/30'
          : isImported
            ? 'border-green-200 bg-green-50/20'
            : isConflict
              ? 'border-amber-200 bg-amber-50/10'
              : 'border-gray-200';

        return (
          <div key={key} className={`bg-white rounded-lg p-4 border transition-colors ${cardBorder}`}>
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-2 min-w-0">
                <div className="flex items-center gap-3 flex-wrap">
                  {aircraftId ? (
                    <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-bold border border-blue-200">{selectedSerial}</span>
                  ) : session.aircraft_serial ? (
                    <span className="px-2 py-0.5 bg-amber-100 text-amber-700 rounded text-xs font-bold border border-amber-200">{session.aircraft_serial}（将自动创建）</span>
                  ) : selectedModelId ? (
                    <span className="px-2 py-0.5 bg-red-50 text-red-500 rounded text-xs font-medium border border-red-200">需要分配飞机</span>
                  ) : (
                    <span className="px-2 py-0.5 bg-gray-100 text-gray-400 rounded text-xs border border-gray-200">请先选择机型</span>
                  )}
                  <span className="text-sm font-mono text-gray-700">{session.session_key || '(默认场次)'}</span>
                  {!isImported && (
                    <label className="flex items-center gap-1 text-xs text-gray-500">
                      <span>时间</span>
                      <input
                        type="date"
                        required
                        value={flightDate}
                        onChange={(event) => onDateChange(key, event.target.value)}
                        className={`bg-white border rounded px-1.5 py-0.5 text-xs text-gray-700 focus:outline-none focus:border-blue-500 ${flightDate ? 'border-gray-300' : 'border-red-300'}`}
                      />
                    </label>
                  )}

                  {selectedModelId && !isImported && (
                    <div className="flex items-center gap-1">
                      <select
                        value={aircraftId ?? ''}
                        onChange={(event) => onAssignAircraft(key, event.target.value ? Number(event.target.value) : null)}
                        className="bg-white border border-gray-300 rounded px-1.5 py-0.5 text-xs"
                      >
                        <option value="">选择已有飞机...</option>
                        {aircraft.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                      </select>
                      {showCreateAircraft[key] ? (
                        <div className="flex items-center gap-1">
                          <input
                            type="text"
                            value={newAircraftSerial}
                            onChange={(event) => setNewAircraftSerial(event.target.value)}
                            placeholder={session.aircraft_serial || '输入飞机序号'}
                            className="bg-white border border-blue-400 rounded px-1 py-0.5 text-xs w-24 focus:outline-none"
                            onKeyDown={(event) => {
                              if (event.key === 'Enter') createAircraft(newAircraftSerial || session.aircraft_serial, key);
                            }}
                          />
                          <button onClick={() => createAircraft(newAircraftSerial || session.aircraft_serial, key)} className="text-[10px] px-1.5 py-0.5 bg-blue-600 text-white rounded hover:bg-blue-500">创建</button>
                          <button onClick={() => setShowCreateAircraft((previous) => ({ ...previous, [key]: false }))} className="text-[10px] px-1.5 py-0.5 bg-gray-200 text-gray-600 rounded hover:bg-gray-300">取消</button>
                        </div>
                      ) : (
                        <button
                          onClick={() => { setShowCreateAircraft((previous) => ({ ...previous, [key]: true })); setNewAircraftSerial(session.aircraft_serial || ''); }}
                          className="text-[10px] text-blue-600 hover:text-blue-500 whitespace-nowrap"
                        >+ 新飞机</button>
                      )}
                    </div>
                  )}

                  {isImporting && <span className="text-xs text-blue-500 animate-pulse">⏳ 导入中...</span>}
                  {isImported && !isImporting && <span className="text-xs text-green-600 font-medium">✓ 已导入</span>}
                  {isConflict && !isImported && !isImporting && <span className="text-xs text-amber-600 font-medium">⚠ 存在冲突</span>}
                  {error && <span className="text-xs text-red-500" title={error}>✗ 失败</span>}
                  <span className="text-xs text-gray-400">{session.file_count} 个文件</span>
                  {session.record_defaults && <span className="text-xs text-emerald-600" title={session.record_source || 'FlightRecord XML'}>XML预填</span>}
                  {session.record_defaults_error && <span className="text-xs text-red-500" title={session.record_defaults_error}>XML错误</span>}
                  {status === 'imported' && session.existing_flight_name && <span className="text-[10px] text-gray-400">当前: {session.existing_flight_name}</span>}
                </div>

                {isConflict && session.conflicting_aircraft && (
                  <div className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded px-2 py-1">
                    ⚠ 飞机「{session.conflicting_aircraft.map((item) => item.aircraft_serial).join('、')}」已导入此日期+时间的飞行。如当前确认为不同飞机，可继续导入。
                  </div>
                )}
                <DataTypeBadges dataTypes={session.data_types} />
                {!isImported && (
                  <div className="mt-3 rounded border border-gray-200 bg-gray-50 p-3 space-y-3">
                    <FlightRecordForm value={record} onChange={(patch) => onRecordChange(key, patch)} variant="import" />
                  </div>
                )}
                {error && <p className="text-xs text-red-500">{error}</p>}
              </div>
              <div className="shrink-0 flex items-center gap-2">
                {!isImported && !isImporting && (
                  <button
                    onClick={() => onImport(session)}
                    disabled={(!selectedModelId && !aircraftId) || !flightDate}
                    className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white rounded text-xs font-medium"
                    title={!flightDate ? '请先填写飞行日期' : (isConflict ? '该日期+时间已有其他飞机导入，如确认为不同飞机则可导入' : (!selectedModelId && !aircraftId ? '请先选择机型' : '导入'))}
                  >导入</button>
                )}
                {isImporting && <button disabled className="px-3 py-1 bg-gray-200 text-gray-400 rounded text-xs cursor-not-allowed">导入中...</button>}
                {error && <button onClick={() => onImport(session)} className="px-2 py-1 text-xs text-blue-600 hover:text-blue-500">重试</button>}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
