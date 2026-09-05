import { Rail } from "@/components/Rail";

// The reader's chrome. The landing at `/` has its own rail — one that floats
// over the room and carries the corpus readout instead of navigation — so the
// header belongs to the surfaces that are used rather than to the root layout.
export default function DemoLayout({ children }: LayoutProps<"/demo">) {
  return (
    <>
      <Rail />
      {children}
    </>
  );
}
