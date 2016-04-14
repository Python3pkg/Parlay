function ParlayItemSearchController($scope, $mdSidenav, ParlayItemManager) {

    /**
     * Create filter function for a query string
     * @param {String} query - Name of item to query by.
     */
    function createFilterFor(query) {
        var lowercase_query = angular.lowercase(query);

        return function filterFn(item) {
            return item.matchesQuery(lowercase_query);
        };
    }
            
    $scope.selected_item = null;
    $scope.search_text = null;
    
    this.selectItem = function (item) {
        // Change is detected after we set item to null.
        if (item === null || item === undefined) return;
        ParlayItemManager.activateItem(item);
        $scope.selected_item = null;
        $scope.search_text = null;

        // Hide sidenav after selecting item on smaller screen sizes where the sidenav is initially hidden.
        if (!$mdSidenav("navigation").isLockedOpen()) {
            $mdSidenav("navigation").close();
        }
    };

    /**
     * Search for items.
     * @param {String} query - Name of item to find.
     */
    this.querySearch = function(query) {
        return query ? ParlayItemManager.getAvailableItems().filter(createFilterFor(query)) : ParlayItemManager.getAvailableItems();
    };
    
    this.hasDiscovered = function () {
	    return ParlayItemManager.hasDiscovered();
    };
    
}

function ParlayItemSearch() {
    return {
        scope: {},
        templateUrl: '../parlay_components/items/directives/parlay-item-search.html',
        controller: 'ParlayItemSearchController',
        controllerAs: "ctrl"
    };
}

angular.module('parlay.items.search', ['templates-main', 'parlay.items.manager'])
	.controller('ParlayItemSearchController', ['$scope', '$mdSidenav', 'ParlayItemManager', ParlayItemSearchController])
	.directive('parlayItemSearch', [ParlayItemSearch]);