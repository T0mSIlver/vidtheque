"use client";

import { dashboard } from "@/lib/dashboard/client";
import { useResource } from "@/lib/dashboard/resource";
import controls from "./kit/controls.module.css";

/**
 * The `channel` filter as a select over every stored name (§29.1). A name in
 * the URL that the list lacks stays an option, so the filter the query ran
 * with is never dropped; until the list lands, that name is the only one.
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
  // The filter matches without case, so `gpu mode` selects `GPU MODE`.
  const match = names.find((row) => row.channel.toLowerCase() === value.toLowerCase());
  const known = match !== undefined;

  return (
    <div className={`${controls.field} ${controls.pickField}`}>
      <label htmlFor={id}>{label}</label>
      <span className={controls.pick}>
        <select
          defaultValue={match?.channel ?? value}
          id={id}
          key={`${known}-${names.length}`}
          name="channel"
        >
          <option value="">all channels</option>
          {value && !known ? <option value={value}>{value}</option> : null}
          {names.map((row) => (
            <option key={row.channel} value={row.channel}>
              {row.channel}
            </option>
          ))}
        </select>
      </span>
    </div>
  );
}
