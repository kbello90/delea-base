export interface MetricProps {
  label: string;
  value: string;
}

export function Metric({ label, value }: MetricProps) {
  return (
    <div className="dl-metric">
      <div className="dl-metric__label">{label}</div>
      <div className="dl-metric__value">{value}</div>
    </div>
  );
}
