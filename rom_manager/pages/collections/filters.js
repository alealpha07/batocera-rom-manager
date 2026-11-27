"use strict";

{
  const filtersForm = document.getElementById("filters-form");
  const previewCount = document.getElementById("preview-count");
  const previewList = document.getElementById("preview-list");
  const previewMore = document.getElementById("preview-more");
  let latestRequest = 0;

  const listItem = (game) => {
    const item = document.createElement("li");
    item.className = "list-group-item";
    const link = document.createElement("a");
    link.href = game.url;
    link.textContent = game.name;
    const system = document.createElement("span");
    system.className = "small text-body-secondary";
    system.textContent = game.fullname;
    item.append(link, " ", system);
    return item;
  };

  const showPreview = (data) => {
    previewCount.textContent = data.count;
    if (data.games.length) {
      previewList.replaceChildren(...data.games.map(listItem));
    } else {
      const empty = document.createElement("li");
      empty.className = "list-group-item text-body-secondary";
      empty.textContent = "No games match";
      previewList.replaceChildren(empty);
    }
    previewMore.textContent = "Only the first " + data.games.length + " are listed.";
    previewMore.hidden = data.count <= data.games.length;
  };

  const updatePreview = async () => {
    const request = ++latestRequest;
    const query = new URLSearchParams(new FormData(filtersForm));
    try {
      const response = await fetch(filtersForm.dataset.preview + "?" + query, {cache: "no-store"});
      if (response.ok && request === latestRequest) {
        showPreview(await response.json());
      }
    } catch (error) {
      if (request === latestRequest) {
        previewCount.textContent = "?";
      }
    }
  };

  filtersForm.addEventListener("change", updatePreview);
}
