import { useEffect, useMemo, useState } from 'react';
import { geoNaturalEarth1, geoPath } from 'd3-geo';
import { feature } from 'topojson-client';
import type { Feature, FeatureCollection, Geometry } from 'geojson';

export interface CountryMapPoint {
  code: string;
  name: string;
  count: number;
  lat: number;
  lng: number;
  color?: string;
}

interface RealWorldMapProps {
  points: CountryMapPoint[];
  onCountryClick?: (point: CountryMapPoint) => void;
}

type CountryFeature = Feature<Geometry, Record<string, unknown>>;

export function RealWorldMap({ points, onCountryClick }: RealWorldMapProps) {
  const [countries, setCountries] = useState<CountryFeature[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetch('/data/world-110m.json')
      .then((response) => response.json())
      .then((topology) => {
        if (cancelled) return;
        const countryObject = topology?.objects?.countries;
        if (!countryObject) return;
        const collection = feature(topology, countryObject) as unknown as FeatureCollection<Geometry, Record<string, unknown>>;
        setCountries(collection.features || []);
      })
      .catch(() => {
        if (!cancelled) setCountries([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const projection = useMemo(() => geoNaturalEarth1().scale(105).translate([310, 122]), []);
  const pathGen = useMemo(() => geoPath(projection), [projection]);
  const projectedPoints = useMemo(
    () =>
      points
        .map((point) => {
          const projected = projection([point.lng, point.lat]);
          if (!projected) return null;
          return { ...point, x: projected[0], y: projected[1] };
        })
        .filter((point): point is CountryMapPoint & { x: number; y: number } => Boolean(point)),
    [points, projection],
  );

  return (
    <svg viewBox="0 0 620 240" preserveAspectRatio="xMidYMid meet" aria-label="KOL 国家分布真地图">
      <defs>
        <linearGradient id="land-real" x1="0" x2="1">
          <stop offset="0" stopColor="#dceaff" />
          <stop offset="1" stopColor="#eef5ff" />
        </linearGradient>
        <filter id="point-glow-real">
          <feGaussianBlur stdDeviation="4" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <g className="world-map-grid">
        {[70, 150, 230, 310, 390, 470, 550].map((x) => <line key={`x-${x}`} x1={x} y1="24" x2={x} y2="220" />)}
        {[54, 92, 130, 168, 206].map((y) => <line key={`y-${y}`} x1="34" y1={y} x2="586" y2={y} />)}
      </g>
      <g className="world-map-real-land" aria-hidden="true">
        {countries.map((country, index) => <path d={pathGen(country) || ''} key={String(country.id || index)} />)}
      </g>
      {projectedPoints.map((point) => {
        const radius = Math.max(5, Math.min(15, 5 + Math.sqrt(point.count || 1) / 2.6));
        const color = point.color || '#1b6cff';
        return (
          <g
            className="map-point"
            key={point.code}
            role="button"
            tabIndex={0}
            onClick={() => onCountryClick?.(point)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') onCountryClick?.(point);
            }}
          >
            <circle cx={point.x} cy={point.y} r={radius * 3.1} fill={color} opacity=".13" />
            <circle cx={point.x} cy={point.y} r={radius} fill={color} filter="url(#point-glow-real)" />
            <text x={point.x + 12} y={point.y + 4} fill="#344054" fontSize="11" fontWeight="800">
              {point.code}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
