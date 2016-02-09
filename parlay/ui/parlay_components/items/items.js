/**
 * @name ItemsConfiguration
 * @param $stateProvider - Service provided by ui.router
 * @description
 * The ItemsConfiguration sets up the items state. The data object is passed with the display name and icon.
 */
function ItemsConfiguration($stateProvider) {
    $stateProvider.state("items", {
        url: "/items",
        templateUrl: "../parlay_components/items/views/base.html",
        controller: "ParlayItemController",
        controllerAs: "itemCtrl"
    });
}

angular.module("parlay.items", ["ui.router", "parlay.items.controller"])
	.config(["$stateProvider", ItemsConfiguration]);