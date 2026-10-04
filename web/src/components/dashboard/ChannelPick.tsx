"use client";

import { dashboard } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import controls from "./kit/controls.module.css";

/**
 * The `channel` filter as a select over every stored name (§28.5). The URL's
 * own spelling is always an option, so neither the first paint nor the band's
 * re-seed drops the filter the query ran with.
 */
export function ChannelPick({
  id,
  value,
  label = "Channel",
}: {
  id: string;
  value: string;
  label?: string;
}) {
  const list = useResource("channels", (signal) => dashboard.channels(signal));
  const names = list.data?.channels.rows ?? [];
  // A stored name differing only in case is the same filter: not offered twice.
  const exact = names.some((row) => row.channel === value);

  return (
    <div className={`${controls.field} ${controls.pickField}`}>
      <label htmlFor={id}>{label}</label>
      <span className={controls.pick}>
        <select defaultValue={value} id={id} key={names.length} name="channel">
          <option value="">all channels</option>
          {value && !exact ? <option value={value}>{value}</option> : null}
          {names
            .filter((row) => exact || row.channel.toLowerCase() !== value.toLowerCase())
            .map((row) => (
              <option key={row.channel} value={row.channel}>
                {row.channel}
              </option>
            ))}
        </select>
      </span>
    </div>
  );
}
