import {redirect} from "next/navigation";

/**
 * `/settings` on its own is where people land by trimming the address bar, and
 * it had no page: only its tabs did, so it answered 404. Connections is the tab
 * that most often needs someone's attention, so it is where this goes.
 */
export default function SettingsIndex(): never {
  redirect("/settings/connectors");
}
