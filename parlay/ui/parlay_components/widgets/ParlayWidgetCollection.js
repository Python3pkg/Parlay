(function () {
    "use strict";

    var module_dependencies = [];

    angular
        .module("parlay.widget.collection", module_dependencies)
        .factory("ParlayWidgetCollection", ParlayWidgetCollectionFactory);

    function ParlayWidgetCollectionFactory() {

        function ParlayWidgetCollection() {
            this.available_widgets = [];
        }

        ParlayWidgetCollection.prototype.registerWidget = function (element, type) {
            this.available_widgets.push({name: element, type: type});
        };

        ParlayWidgetCollection.prototype.getAvailableWidgets = function () {
            return this.available_widgets;
        };

        return new ParlayWidgetCollection();
    }

}());