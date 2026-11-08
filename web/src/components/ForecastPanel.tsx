import { useQuery } from "@tanstack/react-query";
import { fetchForecast } from "@/api/client";
import { ForecastChart } from "@/components/ForecastChart";

export const FORECAST_KEY = ["forecast"] as const;

/** The spending forecast on the Overview; hidden until the API has a band to draw. */
export function ForecastPanel() {
  const { data } = useQuery({ queryKey: FORECAST_KEY, queryFn: fetchForecast });
  if (!Array.isArray(data)) return null; // loading, or an older server
  return <ForecastChart months={data.filter((b) => typeof b?.point === "number")} />;
}
