function ParlayErrorDialog($mdDialog, $mdMedia) {

    return {
        show: function (content) {
            $mdDialog.show({
                controller: "ParlayErrorDialogController",
                controllerAs: "ctrl",
                templateUrl: "../parlay_components/notification/directives/parlay-error-dialog.html",
                locals: {
                    content: content
                },
                bindToController: true,
                clickOutsideToClose: true,
                fullscreen: !$mdMedia("gt-sm")
            });
        }
    };

}

function ParlayErrorDialogController($mdDialog) {

    this.close = function () {
        $mdDialog.hide();
    };

}

angular.module("parlay.notification.error", ["ngMaterial", "parlay.notification"])
    .controller("ParlayErrorDialogController", ["$mdDialog", ParlayErrorDialogController])
    .factory("ParlayErrorDialog", ["$mdDialog", "$mdMedia", ParlayErrorDialog]);