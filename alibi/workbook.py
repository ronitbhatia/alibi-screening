"""Streaming reader for the values in the supplied OOXML browsing workbook."""
import posixpath
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


class Workbook:
    def __init__(self, archive: ZipFile):
        self.archive = archive
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.get("Id"): r.get("Target") for r in relationships}
        self.sheets = {}
        for sheet in ET.fromstring(archive.read("xl/workbook.xml")).iter(NS + "sheet"):
            target = targets[sheet.get(REL)]
            self.sheets[sheet.get("name")] = posixpath.normpath(posixpath.join("xl", target)).lstrip("/")
        self.strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as source:
                for _, item in ET.iterparse(source):
                    if item.tag == NS + "si":
                        self.strings.append("".join(t.text or "" for t in item.iter(NS + "t")))
                        item.clear()

    def rows(self, sheet):
        with self.archive.open(self.sheets[sheet]) as source:
            for _, row in ET.iterparse(source):
                if row.tag != NS + "row":
                    continue
                values = []
                for cell in row.iter(NS + "c"):
                    col = 0
                    for ch in re.match(r"[A-Z]+", cell.attrib["r"])[0]:
                        col = col * 26 + ord(ch) - 64
                    value = cell.findtext(NS + "v")
                    kind = cell.get("t", "n")
                    if kind == "inlineStr":
                        value = "".join(t.text or "" for t in cell.iter(NS + "t"))
                    elif value is not None:
                        if kind == "s":
                            value = self.strings[int(value)]
                        elif kind == "n":
                            value = float(value) if any(c in value for c in ".eE") else int(value)
                        elif kind == "b":
                            value = value == "1"
                    values.extend([None] * (col - len(values)))
                    values[col-1] = value
                row.clear()
                yield values
