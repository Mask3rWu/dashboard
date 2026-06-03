import { useState, useEffect } from 'react';
import ImportPage from './pages/ImportPage';
import FlightView from './pages/FlightView';
import ComparePage from './pages/ComparePage';
import { listFlights, type Flight } from './api';

type Tab = 'import' | 'flight' | 'compare';

export default function App() {
  const [tab, setTab] = useState<Tab>('flight');
  const [flights, setFlights] = useState<Flight[]>([]);
  const [selectedFlightId, setSelectedFlightId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);

  const loadFlights = async () => {
    try {
      const data = await listFlights();
      setFlights(data.flights);
      if (data.flights.length > 0 && !selectedFlightId) {
        setSelectedFlightId(data.flights[0].id);
      }
    } catch (e) {
      console.error('Failed to load flights:', e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadFlights(); }, []);

  const tabs: { key: Tab; label: string }[] = [
    { key: 'import', label: '导入数据' },
    { key: 'flight', label: '飞行分析' },
    { key: 'compare', label: '飞行对比' },
  ];

  return (
    <div className="h-screen flex flex-col bg-white text-gray-900">
      <header className="flex items-center justify-between shrink-0 border-b border-gray-200 px-6 h-14 bg-gray-50">
        <div className="flex items-center gap-6">
          <h1 className="text-lg font-bold text-blue-600 tracking-wide">Flight Analyzer</h1>
          <nav className="flex gap-1">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  tab === t.key
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                }`}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </div>
        {flights.length > 0 && (
          <span className="text-xs text-gray-400">{flights.length} 架次已导入</span>
        )}
      </header>
      <main className="flex-1 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center h-full text-gray-400">加载中...</div>
        ) : (
          <>
            {tab === 'import' && <ImportPage onImported={loadFlights} />}
            {tab === 'flight' && (
              <FlightView
                flights={flights}
                selectedFlightId={selectedFlightId}
                onSelectFlight={setSelectedFlightId}
              />
            )}
            {tab === 'compare' && <ComparePage flights={flights} />}
          </>
        )}
      </main>
    </div>
  );
}
