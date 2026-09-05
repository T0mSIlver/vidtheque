import { Rail } from "@/components/Rail";

// Library browsing keeps the reader's rail, which moved out of the root layout
// when `/` became the landing (which brings its own).
export default function VideosLayout({ children }: LayoutProps<"/videos">) {
  return (
    <>
      <Rail />
      {children}
    </>
  );
}
