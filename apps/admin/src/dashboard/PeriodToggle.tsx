import type { Period } from "./usePeriod";

const OPTIONS: { value: Period; label: string }[] = [
  { value: "mtd", label: "Month to date" },
  { value: "ytd", label: "Year to date" },
];

/** Native radio buttons in a fieldset: a radiogroup with arrow-key support built in. */
export function PeriodToggle({ value, onChange }: { value: Period; onChange: (period: Period) => void }) {
  return (
    <fieldset className="segmented">
      <legend className="visually-hidden">Period</legend>
      {OPTIONS.map((option) => (
        <span key={option.value} className="segmented__option">
          <input
            type="radio"
            id={`period-${option.value}`}
            name="dashboard-period"
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          <label htmlFor={`period-${option.value}`}>{option.label}</label>
        </span>
      ))}
    </fieldset>
  );
}
