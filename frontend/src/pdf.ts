import { toCanvas } from "html-to-image";
import { jsPDF } from "jspdf";
export async function savePaper(element: HTMLElement, filename: string) {
  await document.fonts.ready;
  const image = await toCanvas(element, {
    backgroundColor: "#fff",
    pixelRatio: 1.5,
  });
  const pdf = new jsPDF({ unit: "mm", format: "a4", compress: true });
  const width = 186,
    height = 273;
  const sliceHeight = Math.max(1, Math.floor((image.width * height) / width));
  for (let top = 0; top < image.height; top += sliceHeight) {
    if (top) pdf.addPage();
    const slice = document.createElement("canvas");
    slice.width = image.width;
    slice.height = Math.min(sliceHeight, image.height - top);
    const ctx = slice.getContext("2d");
    if (!ctx) throw new Error("浏览器无法生成 PDF。");
    ctx.drawImage(
      image,
      0,
      top,
      image.width,
      slice.height,
      0,
      0,
      image.width,
      slice.height,
    );
    pdf.addImage(
      slice.toDataURL("image/png"),
      "PNG",
      12,
      12,
      width,
      (slice.height * width) / image.width,
    );
  }
  pdf.save(filename + ".pdf");
}
