(() => {
    "use strict";

    /*
     * ============================================================
     * FOUNDER HIGHLIGHT VIDEO UPLOAD
     * ============================================================
     */

    const fileInput = document.getElementById(
        "highlight-video-file"
    );

    const uploadButton = document.getElementById(
        "highlight-upload-button"
    );

    const statusElement = document.getElementById(
        "highlight-upload-status"
    );

    const videoUrlInput = document.getElementById(
        "highlight-video-url"
    );

    if (
        fileInput &&
        uploadButton &&
        statusElement &&
        videoUrlInput
    ) {
        let selectedFile = null;

        fileInput.addEventListener("change", () => {
            selectedFile = fileInput.files[0] || null;

            if (!selectedFile) {
                statusElement.textContent =
                    "Please choose a video first.";
                return;
            }

            statusElement.textContent =
                `Selected: ${selectedFile.name}. ` +
                "Tap UPLOAD VIDEO to continue.";
        });

        uploadButton.addEventListener(
            "click",
            async () => {
                const file =
                    selectedFile ||
                    fileInput.files[0] ||
                    null;

                if (!file) {
                    statusElement.textContent =
                        "Please choose a video first.";
                    return;
                }

                uploadButton.disabled = true;
                fileInput.disabled = true;

                statusElement.textContent =
                    "Uploading video... Please wait.";

                const formData = new FormData();
                formData.append("video", file);

                try {
                    const response = await fetch(
                        "/admin/founder/highlights/upload",
                        {
                            method: "POST",
                            body: formData,
                            credentials: "same-origin"
                        }
                    );

                    const contentType =
                        response.headers.get(
                            "content-type"
                        ) || "";

                    if (
                        !contentType.includes(
                            "application/json"
                        )
                    ) {
                        throw new Error(
                            "The server returned an unexpected response."
                        );
                    }

                    const data =
                        await response.json();

                    if (
                        !response.ok ||
                        !data.success
                    ) {
                        throw new Error(
                            data.error ||
                            "Video upload failed."
                        );
                    }

                    if (
                        typeof data.video_url !==
                            "string" ||
                        !data.video_url.trim()
                    ) {
                        throw new Error(
                            "The server did not return a valid video URL."
                        );
                    }

                    videoUrlInput.value =
                        data.video_url.trim();

                    statusElement.textContent =
                        "Video uploaded successfully. " +
                        "You can now publish the highlight.";

                } catch (error) {
                    statusElement.textContent =
                        error.message ||
                        "Video upload failed.";

                    uploadButton.disabled = false;
                    fileInput.disabled = false;
                }
            }
        );
    }


    /*
     * ============================================================
     * PUBLIC HIGHLIGHT VIEW ANALYTICS
     * ============================================================
     */

    const highlightVideos = document.querySelectorAll(
        "video[data-highlight-id]"
    );

    highlightVideos.forEach((video) => {
        video.addEventListener(
            "play",
            async () => {
                if (
                    video.dataset.analyticsTracked ===
                    "1"
                ) {
                    return;
                }

                const highlightId =
                    Number(
                        video.dataset.highlightId
                    );

                if (
                    !Number.isInteger(highlightId) ||
                    highlightId <= 0
                ) {
                    return;
                }

                video.dataset.analyticsTracked =
                    "1";

                try {
                    const response =
                        await fetch(
                            "/analytics/highlight-view",
                            {
                                method: "POST",
                                headers: {
                                    "Content-Type":
                                        "application/json"
                                },
                                credentials:
                                    "same-origin",
                                body: JSON.stringify({
                                    highlight_id:
                                        highlightId
                                })
                            }
                        );

                    if (!response.ok) {
                        video.dataset.analyticsTracked =
                            "0";
                    }
                } catch (error) {
                    video.dataset.analyticsTracked =
                        "0";
                }
            },
            { passive: true }
        );
    });
})();


/* ========================================= */
/* WEBSITE ANALYTICS TREND CHART */
/* ========================================= */

const analyticsChart =
    document.querySelector(
        "[data-analytics-chart]"
    );

const analyticsTrend =
    window.AminationAnalyticsTrend;

if (
    analyticsChart &&
    analyticsTrend &&
    Array.isArray(analyticsTrend.labels) &&
    Array.isArray(analyticsTrend.visitors) &&
    Array.isArray(analyticsTrend.highlight_views)
) {
    const visitorsLine =
        analyticsChart.querySelector(
            "[data-chart-visitors]"
        );

    const highlightsLine =
        analyticsChart.querySelector(
            "[data-chart-highlights]"
        );

    const labelsGroup =
        analyticsChart.querySelector(
            "[data-chart-labels]"
        );

    const visitorPointsGroup =
        analyticsChart.querySelector(
            "[data-chart-points-visitors]"
        );

    const highlightPointsGroup =
        analyticsChart.querySelector(
            "[data-chart-points-highlights]"
        );

    const labels = analyticsTrend.labels;
    const visitors = analyticsTrend.visitors;
    const highlightViews =
        analyticsTrend.highlight_views;

    const pointCount = labels.length;

    if (
        pointCount > 0 &&
        visitors.length === pointCount &&
        highlightViews.length === pointCount
    ) {
        const chartLeft = 60;
        const chartRight = 760;
        const chartTop = 40;
        const chartBottom = 250;

        const chartWidth =
            chartRight - chartLeft;

        const chartHeight =
            chartBottom - chartTop;

        const maximumValue = Math.max(
            1,
            ...visitors.map(
                (value) =>
                    Number.isFinite(Number(value))
                        ? Number(value)
                        : 0
            ),
            ...highlightViews.map(
                (value) =>
                    Number.isFinite(Number(value))
                        ? Number(value)
                        : 0
            )
        );

        const xPosition = (index) => {
            if (pointCount === 1) {
                return chartLeft + (
                    chartWidth / 2
                );
            }

            return (
                chartLeft
                + (
                    index
                    / (pointCount - 1)
                ) * chartWidth
            );
        };

        const yPosition = (value) => {
            const numericValue =
                Number.isFinite(Number(value))
                    ? Number(value)
                    : 0;

            return (
                chartBottom
                - (
                    numericValue
                    / maximumValue
                ) * chartHeight
            );
        };

        const createPolylinePoints =
            (values) => {
                return values
                    .map(
                        (value, index) =>
                            `${xPosition(index)},${yPosition(value)}`
                    )
                    .join(" ");
            };

        visitorsLine.setAttribute(
            "points",
            createPolylinePoints(visitors)
        );

        highlightsLine.setAttribute(
            "points",
            createPolylinePoints(
                highlightViews
            )
        );

        const createSvgElement =
            (tagName) => {
                return document.createElementNS(
                    "http://www.w3.org/2000/svg",
                    tagName
                );
            };

        const labelStep = Math.max(
            1,
            Math.ceil(pointCount / 7)
        );

        labels.forEach(
            (label, index) => {
                if (
                    index % labelStep !== 0 &&
                    index !== pointCount - 1
                ) {
                    return;
                }

                const text =
                    createSvgElement("text");

                text.setAttribute(
                    "x",
                    xPosition(index)
                );

                text.setAttribute(
                    "y",
                    "278"
                );

                text.setAttribute(
                    "text-anchor",
                    "middle"
                );

                text.textContent = String(
                    label
                );

                labelsGroup.appendChild(text);
            }
        );

        const addPoints =
            (values, group) => {
                values.forEach(
                    (value, index) => {
                        const circle =
                            createSvgElement(
                                "circle"
                            );

                        circle.setAttribute(
                            "cx",
                            xPosition(index)
                        );

                        circle.setAttribute(
                            "cy",
                            yPosition(value)
                        );

                        circle.setAttribute(
                            "r",
                            "3"
                        );

                        circle.setAttribute(
                            "data-value",
                            String(value)
                        );

                        circle.setAttribute(
                            "data-label",
                            String(labels[index])
                        );

                        group.appendChild(
                            circle
                        );
                    }
                );
            };

        addPoints(
            visitors,
            visitorPointsGroup
        );

        addPoints(
            highlightViews,
            highlightPointsGroup
        );
    }
}
